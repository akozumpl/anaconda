from __future__ import print_function

import functools
import threading
import traceback

def trc():
    thread = threading.current_thread()
    print('--- --- trace for: %s' % thread.name)
    tb = traceback.extract_stack()
    print_no_newline = functools.partial(print, end='')
    map(print_no_newline, traceback.format_list(tb[:-1]))
    # traceback.print_stack(tb)
    print('--- ---')
