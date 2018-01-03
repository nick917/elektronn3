import gzip
import logging
import os
import pickle as pkl
import signal
import sys
import time
from functools import wraps

import h5py
import numpy as np
import torch
from torch.autograd import Variable

from elektronn3 import floatX

logger = logging.getLogger("elektronn3log")


def to_variable(array: np.ndarray, volatile=True, cuda='auto') -> Variable:
    if cuda == 'auto':
        cuda = torch.cuda.is_available()
    tensor = torch.from_numpy(array)
    if cuda:
        tensor = tensor.cuda()
    var = Variable(tensor, volatile=volatile)
    return var


def get_filepaths_from_dir(directory, ending='k.zip', recursively=False):
    """
    Collect all files with certain ending from directory.

    Parameters
    ----------
    directory: str
        path to lookup directory
    ending: str
        ending of files
    recursively: boolean
        add files from subdirectories

    Returns
    -------
    list of str
        paths to files
    """
    if recursively:
        files = [os.path.join(r, f) for r,s ,fs in
                 os.walk(directory) for f in fs if ending in f[-len(ending):]]
    else:
        files = [os.path.join(directory, f) for f in next(os.walk(directory))[2]
                 if ending in f[-len(ending):]]
    return files


def save_to_h5py(data, path, hdf5_names=None, overwrite=False, compression=True):
    """
    Saves data to h5py File.

    Parameters
    ----------
    data: list or dict of np.arrays
        if list, hdf5_names has to be set.
    path: str
        forward-slash separated path to file
    hdf5_names: list of str
        has to be the same length as data
    overwrite : bool
        determines whether existing files are overwritten
    compression : bool
        True: compression='gzip' is used which is recommended for sparse and
        ordered data

    Returns
    -------
    nothing

    """
    if (not type(data) is dict) and hdf5_names is None:
        raise Exception("hdf5names has to be set, when data is a list")
    if os.path.isfile(path) and overwrite:
        os.remove(path)
    f = h5py.File(path, "w")
    if type(data) is dict:
        for key in data.keys():
            if compression:
                f.create_dataset(key, data=data[key], compression="gzip")
            else:
                f.create_dataset(key, data=data[key])
    else:
        if len(hdf5_names) != len(data):
            f.close()
            raise Exception("Not enough or to much hdf5-names given!")
        for nb_data in range(len(data)):
            if compression:
                f.create_dataset(hdf5_names[nb_data], data=data[nb_data],
                                 compression="gzip")
            else:
                f.create_dataset(hdf5_names[nb_data], data=data[nb_data])
    f.close()


def h5save(data, file_name, keys=None, compress=True):
    """
    Writes one or many arrays to h5 file

    data:
      single array to save or iterable of arrays to save.
      For iterable all arrays are written to the file.
    file_name: string
      path/name of destination file
    keys: string / list thereof
      For single arrays this is a single string which is used as a name
      for the data set.
      For multiple arrays each dataset is named by the corresponding key.
      If keys is ``None``, the dataset names created by enumeration: ``data%i``
    compress: Bool
      Whether to use lzf compression, defaults to ``True``. Most useful for
      label arrays.
    """
    file_name = os.path.expanduser(file_name)
    compr = 'lzf' if compress else None
    f = h5py.File(file_name, "w")
    if isinstance(data, list) or isinstance(data, tuple):
        if keys is not None:
            assert len(keys)==len(data)
        for i, d in enumerate(data):
            if keys is None:
                f.create_dataset(str(i), data=d, compression=compr)
            else:
                f.create_dataset(keys[i], data=d, compression=compr)
    else:
        if keys is None:
            f.create_dataset('data', data=data, compression=compr)
        else:
            f.create_dataset(keys, data=data, compression=compr)
    f.close()


def h5load(file_name, keys=None):
    """
    Loads data sets from h5 file

    file_name: string
      destination file
    keys: string / list thereof
      Load only data sets specified in keys and return as list in the order
      of ``keys``
      For a single key the data is returned directly - not as list
      If keys is ``None`` all datasets that are listed in the keys-attribute
      of the h5 file are loaded.
    """
    file_name = os.path.expanduser(file_name)
    ret = []
    try:
        f = h5py.File(file_name, "r")
    except IOError:
        raise IOError("Could not open h5-File %s" % (file_name))

    if keys is not None:
        try:
            if isinstance(keys, str):
                ret.append(f[keys].value)
            else:
                for k in keys:
                    ret.append(f[k].value)
        except KeyError:
            raise KeyError("Could not read h5-dataset named %s. Available "
                           "datasets: %s" % (keys, list(f.keys())))
    else:
        for k in f.keys():
            ret.append(f[k].value)

    f.close()

    if len(ret)==1:
        return ret[0]
    else:
        return ret


def picklesave(data, file_name):
    """
    Writes one or many objects to pickle file

    data:
      single objects to save or iterable of objects to save.
      For iterable, all objects are written in this order to the file.
    file_name: string
      path/name of destination file
    """
    file_name = os.path.expanduser(file_name)
    with open(file_name, 'wb') as f:
        pkl.dump(data, f, protocol=2)


def pickleload(file_name):
    """
    Loads all object that are saved in the pickle file.
    Multiple objects are returned as list.
    """
    file_name = os.path.expanduser(file_name)
    ret = []
    try:
        with open(file_name, 'rb') as f:
            try:
                while True:
                    # Python 3 needs explicit encoding specification,
                    # which Python 2 lacks:
                    if sys.version_info.major >= 3:
                        ret.append(pkl.load(f, encoding='latin1'))
                    else:
                        ret.append(pkl.load(f))
            except EOFError:
                pass

        if len(ret) == 1:
            return ret[0]
        else:
            return ret

    except pkl.UnpicklingError:
        with gzip.open(file_name, 'rb') as f:
            try:
                while True:
                    # Python 3 needs explicit encoding specification,
                    # which Python 2 lacks:
                    if sys.version_info.major >= 3:
                        ret.append(pkl.load(f, encoding='latin1'))
                    else:
                        ret.append(pkl.load(f))
            except EOFError:
                pass

        if len(ret) == 1:
            return ret[0]
        else:
            return ret


### Decorator Collection ###
class DecoratorBase:
    """
    If used as
    ``@DecoratorBase``
    this initialiser receives only the function to be wrapped (no wrapper args)
    Then ``__call__`` receives the arguments for the underlying function.

    Alternatively, if used as
    ``@DecoratorBase(wrapper_print=True, n_times=10)``
    this initialiser receives wrapper args, the function is passed to ``__call__``
    and ``__call__`` returns a wrapped function.

    This base class completely ignores all wrapper arguments.
    """

    def __init__(self, *args, **kwargs):
        self.func = None
        self.dec_args = None
        self.dec_kwargs = None
        if len(args)==1 and not len(kwargs):
            assert hasattr(args[0], '__call__')
            func = args[0]
            self.func = func
            self.__call__.__func__.__doc__ = func.__doc__
            self.__call__.__func__.__name__ = func.__name__
        else:
            self.dec_args = args
            self.dec_kwargs = kwargs

    def __call__(self, *args, **kwargs):
        # The decorator was initialised with the func, it now has apply the decoration itself
        if not self.func is None:
            # do something with args
            ret = self.func(*args, **kwargs)
            # do something with kwargs
            return ret

        # The decorator was initialised with args, it now returns a wrapped function
        elif len(args)==1 and not len(kwargs):
            assert hasattr(args[0], '__call__')
            func = args[0]

            @wraps(func)
            def decorated(*args0, **kwargs0):
                # do something with args0, read the decorator arguments
                # print(self.dec_args)
                # print(self.dec_kwargs)
                ret = func(*args0, **kwargs0)
                # do something with ret
                return ret

            return decorated
        else:
            raise ValueError()


class timeit(DecoratorBase):
    def __call__(self, *args, **kwargs):
        # The nor args for the decorator --> n=1
        if not self.func is None:
            t0 = time.time()
            ret = self.func(*args, **kwargs)
            t = time.time() - t0
            print("Function <%s> took %.5g s" % (self.func.__name__, t))
            return ret

        # The decorator was initialised with args, it now returns a wrapped function
        elif len(args)==1 and not len(kwargs):
            assert hasattr(args[0], '__call__')
            func = args[0]
            n = self.dec_kwargs.get('n', 1)

            @wraps(func)
            def decorated(*args0, **kwargs0):
                t0 = time.time()
                if n>1:
                    for i in range(n - 1):
                        func(*args0, **kwargs0)

                ret = func(*args0, **kwargs0)
                t = time.time() - t0
                print("Function <%s> took %.5g s averaged over %i execs" % (
                    func.__name__, t / n, n))

                return ret

            return decorated

        else:
            raise ValueError()


def as_floatX(x):
    if not hasattr(x, '__len__'):
        return np.array(x, dtype=floatX)
    return np.ascontiguousarray(x, dtype=floatX)


# https://gist.github.com/tcwalther/ae058c64d5d9078a9f333913718bba95
# class based on: http://stackoverflow.com/a/21919644/487556
class DelayedInterrupt:
    def __init__(self, signals):
        if not isinstance(signals, list) and not isinstance(signals, tuple):
            signals = [signals]
        self.sigs = signals

    def __enter__(self):
        self.signal_received = {}
        self.old_handlers = {}
        for sig in self.sigs:
            self.signal_received[sig] = False
            self.old_handlers[sig] = signal.getsignal(sig)
            def handler(s, frame):
                logger.warning('Signal %s received. Delaying KeyboardInterrupt.' % sig)
                self.signal_received[sig] = (s, frame)
                # Note: in Python 3.5, you can use signal.Signals(sig).name
            self.old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)

    def __exit__(self, type, value, traceback):
        for sig in self.sigs:
            signal.signal(sig, self.old_handlers[sig])
            if self.signal_received[sig] and self.old_handlers[sig]:
                self.old_handlers[sig](*self.signal_received[sig])


class CleanExit:
    # https://stackoverflow.com/questions/4205317/capture-keyboardinterrupt-in-python-without-try-except
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        if exc_type is KeyboardInterrupt:
            logger.warning('Delaying KeyboardInterrupt.')
            return True
        return exc_type is None


class GracefulInterrupt:
    # by https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully
    now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, sig, frame):
        logger.warning('Signal %s received. Delaying KeyboardInterrupt.' % sig)
        self.now = True
