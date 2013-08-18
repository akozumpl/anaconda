#! /usr/bin/python

from __future__ import print_function
from __future__ import absolute_import

import functools
import glob
import os
import pylorax.executils
import pylorax.imgutils
import pylorax.sysutils
import shutil
import subprocess
import sys

IMAGE = os.path.abspath('./image.img')
KS = os.path.abspath('./go.ks')
ROOT_PATH = '/mnt/sysimage/'
SIZE_GB = 8 * 1024 ** 3
ANACONDA_CHECKOUT = '/home/akozumpl/anaconda'
ANACONDA_EXECUTABLE = os.path.join(ANACONDA_CHECKOUT, 'anaconda')

def ignore_oserror(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OSError:
        pass

def cleanup_logs():
    ignore_oserror(shutil.rmtree, '/tmp/payload-logs')
    for log in glob.glob('/tmp/*.log'):
        ignore_oserror(os.remove, log)

def remove_image():
    try:
        pylorax.sysutils.remove(IMAGE)
    except OSError:
        pass

def run_anaconda(program, args):
    line = '%s %s' % (program, ' '.join(args))
    print(line)
    return os.system(line) >> 8 # retval is os.wait() encoded

def try_umount(mount_point):
    try:
        pylorax.imgutils.umount(mount_point, retrysleep=0)
    except subprocess.CalledProcessError:
        pass

def main():
    remove_image()

    pylorax.imgutils.mkext4img(None, IMAGE, label="nukes", size=SIZE_GB)
    if not os.path.isdir(ROOT_PATH):
        os.mkdir(ROOT_PATH)
    pylorax.imgutils.mount(IMAGE, opts='loop', mnt=ROOT_PATH)
    args = ['--kickstart', KS, '--cmdline', '--dirinstall']
    retval = None
    cleanup_logs()
    os.putenv('PYTHONPATH', os.getenv('SUDO_PYTHONPATH'))
    try:
        retval = run_anaconda(ANACONDA_EXECUTABLE, args)
    finally:
        paths = ('sys', 'run', 'dev/pts', 'dev/shm', 'dev', 'proc', '')
        full_paths = map(functools.partial(os.path.join, ROOT_PATH), paths)
        map(try_umount, full_paths)
        if retval != 0:
            remove_image()
    print('Done.')

if __name__ == '__main__':
    main()
