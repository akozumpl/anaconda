#! /usr/bin/python

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
DNF_CHECKOUT = '/home/akozumpl/dnf'
HAWKEY_CHECKOUT = '/' # here

def run_anaconda(program, args):
    line = '%s %s' % (program, ' '.join(args))
    print(line)
    return os.system(line)

def main():
    try:
        pylorax.sysutils.remove(IMAGE)
    except OSError:
        pass

    pylorax.imgutils.mkext4img(None, IMAGE, label="nukes", size=SIZE_GB)
    if not os.path.isdir(ROOT_PATH):
        os.mkdir(ROOT_PATH)
    pylorax.imgutils.mount(IMAGE, opts='loop', mnt=ROOT_PATH)
    args = ['--dnf', '--kickstart', KS, '--cmdline', '--dirinstall']
    try:
        pythonpath = ':'.join((ANACONDA_CHECKOUT, DNF_CHECKOUT))
        os.putenv('PYTHONPATH', pythonpath)
        run_anaconda('anaconda', args)
    finally:
        pylorax.imgutils.umount(ROOT_PATH)
    print('quitting')

if __name__ == '__main__':
    main()
