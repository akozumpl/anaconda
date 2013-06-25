#! /usr/bin/python

from __future__ import print_function
from __future__ import absolute_import

import os
import pylorax.executils
import pylorax.imgutils
import pylorax.sysutils
import sys

IMAGE = os.path.abspath('./image.img')
KS = os.path.abspath('./go.ks')
ROOT_PATH = '/mnt/sysimage/'
SIZE_GB = 8 * 1024 ** 3
ANACONDA_CHECKOUT = '/home/akozumpl/repos/anaconda/a.f'
ANACONDA_EXECUTABLE = os.path.join(ANACONDA_CHECKOUT, 'anaconda')
DNF_CHECKOUT = '/home/akozumpl/dnf'
HAWKEY_CHECKOUT = '/' # here

def remove_image():
    try:
        pylorax.sysutils.remove(IMAGE)
    except OSError:
        pass

def run_anaconda(program, args):
    line = '%s %s' % (program, ' '.join(args))
    print(line)
    return os.system(line) >> 8 # retval is os.wait() encoded

def main():
    remove_image()

    pylorax.imgutils.mkext4img(None, IMAGE, label="nukes", size=SIZE_GB)
    if not os.path.isdir(ROOT_PATH):
        os.mkdir(ROOT_PATH)
    pylorax.imgutils.mount(IMAGE, opts='loop', mnt=ROOT_PATH)
    args = ['--dnf', '--kickstart', KS, '--cmdline', '--dirinstall']
    retval = None
    try:
        pythonpath = ':'.join((ANACONDA_CHECKOUT, DNF_CHECKOUT))
        os.putenv('PYTHONPATH', pythonpath)
        retval = run_anaconda(ANACONDA_EXECUTABLE, args)
    finally:
        pylorax.imgutils.umount(ROOT_PATH)
        if retval != 0:
            remove_image()
    print('Done.')

if __name__ == '__main__':
    main()
