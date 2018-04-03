#!/usr/bin/env python3

# ELEKTRONN3 - Neural Network Toolkit
#
# Copyright (c) 2017 - now
# Max Planck Institute of Neurobiology, Munich, Germany
# Authors: Martin Drawitsch, Philipp Schubert

import argparse
import datetime
import logging
import os

import numpy as np
import torch
from torch import nn
from torch import optim

# setup logging before importing elektronn3 parts because mpl_backend
# has to be defined first
logger = logging.getLogger('elektronn3log')

parser = argparse.ArgumentParser(description='Train a network.')
parser.add_argument('model_name')
parser.add_argument('--disable-cuda', action='store_true', help='Disable CUDA')
parser.add_argument('--data-config', choices=['wb', 'local'], default='local')
parser.add_argument('--save-name', default=None, help='Manually set save_name')
parser.add_argument(
    '--ignore-errors', action='store_true',
    help='Ignore training errors (You should probably not use this!)'
)
parser.add_argument(
    '--disable-ipython-on-error', action='store_true',
    help='Disable IPython inspection shell on unhandled errors.'
)
parser.add_argument(
    '--epoch-size', type=int, default=100,
    help='How many training steps to perform between '
         'validation/preview/extended-stat calculation phases.'
)
parser.add_argument(
    '--mpl_backend', type=str, default='agg',
    help='How many training steps to perform between '
         'validation/preview/extended-stat calculation phases.'
)
args = parser.parse_args()

model_name = args.model_name
data_config = args.data_config
cuda_enabled = not args.disable_cuda and torch.cuda.is_available()

logger.info('Cuda enabled' if cuda_enabled else 'Cuda disabled')

# Don't move this stuff, it needs to be run this early to work
import elektronn3
mpl_backend = args.mpl_backend
elektronn3.select_mpl_backend(mpl_backend)

from elektronn3.data.cnndata import PatchCreator
from elektronn3.training.trainer import StoppableTrainer
from elektronn3.models.vnet import VNet
from elektronn3.models.fcn import fcn32s
from elektronn3.models.simple import Simple3DNet, Extended3DNet, N3DNet
from elektronn3.models.unet import UNet


# USER PATHS
path_prefix = os.path.expanduser('~/e3training/')
os.makedirs(path_prefix, exist_ok=True)

if args.save_name is None:
    timestamp = datetime.datetime.now().strftime('%y-%m-%d_%H-%M-%S')
    save_name = model_name + '__' + timestamp
else:
    save_name = args.save_name  # TODO: Warn if directory already exists
save_path = os.path.join(path_prefix, save_name)

# potential candidates for CLI arguments
nIters = int(500000)
wd = 0.5e-4
lr = 0.0004
opt = 'amsgrad'
lr_stepsize = 1000
lr_dec = 0.99
batch_size = 1
epoch_size = args.epoch_size

if model_name == 'fcn32s': # Reduce the patch size in case of memory constraints (to eg. (12, 48, 48))
    model = fcn32s(learned_billinear=False)
elif model_name == 'vnet':
    model = VNet(relu=False)
elif model_name == 'simple':
    model = Simple3DNet()
elif model_name == 'extended':
    model = Extended3DNet()
elif model_name == 'n3d':
    model = N3DNet()
elif model_name == 'unet':
    model = UNet(
        n_blocks=4,
        start_filts=32,
        planar_blocks=(1,),
        activation='relu',
        batch_norm=True
    )
else:
    raise ValueError('model not found.')


def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv3d') != -1:
        size = m.weight.size()
        fan_out = size[0]  # number of rows
        fan_in = size[1]  # number of columns
        variance = np.sqrt(2.0 / (fan_in + fan_out))
        m.weight.data.normal_(0.0, variance)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


if data_config == 'local':
    path = os.path.expanduser('~/neuro_data_cdhw/')
    data_init_kwargs = {
        'input_path': path,
        'target_path': path,
        'input_h5data': [('raw_%i.h5' % i, 'raw') for i in range(3)],
        'target_h5data': [('barrier_int16_%i.h5' %i, 'lab') for i in range(3)],
        'mean': 155.291411,
        'std': 41.812504,
        'aniso_factor': 2,
        'source': 'train',
        'patch_shape': (48, 96, 96),
        'preview_shape': (64, 144, 144),
        'valid_cube_indices': [2],
        'grey_augment_channels': [],
        'epoch_size': epoch_size * batch_size,
        'warp': 0.5,
        'class_weights': True,
        'warp_args': {
            'sample_aniso': True,
            'perspective': True
        }
    }
elif data_config == 'wb':  # For internal testing. To be removed later.
    path = os.path.expanduser('~/barrier_gt_phil_cdhw/')
    data_init_kwargs = {
        'input_path': path,
        'target_path': path,
        'input_h5data': [('v2_new_%i-rawbarr-zyx.h5' % i, 'raW') for i in range(29)],
        'target_h5data': [('v2_new_%i-rawbarr-zyx.h5' %i, 'labels') for i in range(29)],
        'mean': 0.617148,
        'std': 0.155292,
        'aniso_factor': 2,
        'source': 'train',
        'patch_shape': (96, 96, 96),
        'preview_shape': (64, 144, 144),
        'valid_cube_indices': [2],
        'grey_augment_channels': [],
        'epoch_size': epoch_size * batch_size,
        'warp': 0.5,
        'class_weights': True,
        'warp_args': {
            'sample_aniso': True,
            'perspective': True
        }
    }
dataset = PatchCreator(**data_init_kwargs, cuda_enabled=cuda_enabled)


torch.manual_seed(0)
if cuda_enabled:
    torch.cuda.manual_seed(0)
if batch_size >= 4 and cuda_enabled:
    model = nn.parallel.DataParallel(model, device_ids=[0, 1])
if cuda_enabled:
    model = model.cuda()
# TODO: make weight init a general parameter, e.g. add support for various existing weight initializations (https://github.com/pytorch/pytorch/blob/master/torch/nn/init.py#L211)
# TODO: For biases it's probably okay to initially set them to 0

if opt == 'sgd':
    optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9, weight_decay=wd)
elif opt == 'adam':
    optimizer = optim.Adam(model.parameters(), weight_decay=wd, lr=lr)
elif opt == 'amsgrad':
    optimizer = optim.Adam(model.parameters(), weight_decay=wd, lr=lr, amsgrad=True)
elif opt == 'rmsprop':
    optimizer = optim.RMSprop(model.parameters(), weight_decay=wd, lr=lr)
else:
    raise NotImplementedError("Optimizer needs to be specified.")

lr_sched = optim.lr_scheduler.StepLR(optimizer, lr_stepsize, lr_dec)
# Reduce LR after loss stagnates on plateau for 10 batches (patience=10)
# lr_sched = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=10,
#                                                 factor=0.5)

criterion = nn.CrossEntropyLoss(weight=dataset.class_weights)
# TODO: Dice loss? (used in original V-Net) https://github.com/mattmacy/torchbiomed/blob/661b3e4411f7e57f4c5cbb56d02998d2d8bddfdb/torchbiomed/loss.py

st = StoppableTrainer(model, criterion=criterion, optimizer=optimizer,
                      dataset=dataset, batchsize=batch_size, num_workers=2,
                      save_path=save_path,
                      schedulers={"lr": lr_sched},
                      cuda_enabled=cuda_enabled,
                      ignore_errors=args.ignore_errors,
                      ipython_on_error=not args.disable_ipython_on_error)
st.train(nIters)
