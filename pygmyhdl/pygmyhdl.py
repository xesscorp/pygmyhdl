# -*- coding: utf-8 -*-

# MIT license
# 
# Copyright (C) 2017 by XESS Corp.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()

import sys
import os
import re
import logging
from copy import deepcopy
from pprint import pprint
import pdb
from myhdl import *
from myhdlpeek import *
from random import randrange
import byteplay3 as bp
import types

logger = logging.getLogger('pygmyhdl')

USING_PYTHON2 = (sys.version_info.major == 2)
USING_PYTHON3 = not USING_PYTHON2

DEBUG_OVERVIEW = logging.DEBUG
DEBUG_DETAILED = logging.DEBUG - 1
DEBUG_OBSESSIVE = logging.DEBUG - 2


# List of MyHDL instances generated by this module.
_instances = list()
_wire_setters = list()

def initialize():
    global _instances, _wire_setters
    _instances = list()
    _wire_setters = list()
    Peeker.clear()

class Wire(SignalType):
    def __init__(self, name=None):
        super().__init__(bool(0))
        if name:
            Peeker(self, name)

class Bus(SignalType):
    def __init__(self, width=1, name=None):
        super().__init__(intbv(0, min=0, max=2**width))
        self.width = width
        self.i_wires = None
        self.o_wires = None
        if name:
            Peeker(self, name)

    @property
    def i(self):
        '''Return a list of wires that will drive this Bus object.'''
        if not self.i_wires:
            self.i_wires = [Wire() for _ in range(self.width)]
            def wires2bus(wires, bus):
                wires_bus = ConcatSignal(*reversed(wires))
                @always_comb
                def xfer():
                    bus.next = wires_bus
                return xfer
            xfer_inst = wires2bus(self.i_wires, self)
            _instances.append(xfer_inst)
        return self.i_wires

    @property
    def o(self):
        '''Return a set of wires carrying the bit values of the Bus wires.'''
        if not self.o_wires:
            self.o_wires = [self(i) for i in range(self.width)]
        return self.o_wires

def simulate(*modules):
    def flatten(nested_list):
        lst = []
        for item in nested_list:
            if isinstance(item, (list, tuple)):
                lst.extend(flatten(item))
            else:
                lst.append(item)
        return lst

    modules = set(flatten(modules))
    for m in modules:
        if m in _instances:
            Simulation(*modules, *_wire_setters, *Peeker.instances()).run()
            return
    Simulation(*modules, *_instances, *_wire_setters, *Peeker.instances()).run()

def get_max(signal):
    return signal.max or 2**len(signal)

def get_min(signal):
    return signal.min or 0

def random_test(num_tests, *wires):
    for _ in range(num_tests):
        for wire in wires:
            wire.next = randrange(get_min(wire), get_max(wire))
        yield delay(1)
    
def exhaustive_test(*wires):
    if len(wires) == 0:
        yield delay(1)
    else:
        for wires[0].next in range(get_min(wires[0]), get_max(wires[0])):
            yield from exhaustive_test(*wires[1:])

def random_sim(num_steps, *wires):
    simulate(random_test(num_steps, *wires))

def exhaustive_sim(*wires):
    simulate(exhaustive_test(*wires))

show_waveforms = Peeker.to_wavedrom


############## @group decorator. #################

def _func_copy(f, new_code) :
    '''
    Return a copy of function f with __code__ section replaced with new_code.
    Copied from https://github.com/tallforasmurf/byteplay/blob/master/examples/make_constants.py.
    '''
    new_func = types.FunctionType( new_code, f.__globals__ )
    new_func.__annotations__ = f.__annotations__
    # new_func.__closure__ = f.__closure__
    new_func.__defaults__ = f.__defaults__
    new_func.__doc__ = f.__doc__
    new_func.__name__ = f.__name__
    new_func.__kwdefaults__ = f.__kwdefaults__
    new_func.__qualname__ = f.__qualname__
    return new_func

def postamble_func(index, myhdl_instances):
    global _instances
    insts = _instances[index:]
    insts.extend(myhdl_instances)
    _instances = _instances[:index]
    _instances.append(insts)
    return insts

def group(f):
    '''
    Decorator for grouping components generated by function f.

    Gets the generator function code section and prepends/appends code to
    observe what components are instantiated in the _instances list and then
    stores them in a local variable so MyHDL can detect them. 
    '''

    # Get the generator function code section.
    f_code = bp.Code.from_code(f.__code__)

    # Add this code to the start to store the beginning index of the _instances list.
    # Python version of preamble:
    #   instances_begin_index = len(pygmyhdl._instances)
    preamble = [
        (bp.LOAD_GLOBAL, 'len'),
        (bp.LOAD_GLOBAL, 'pygmyhdl'),
        (bp.LOAD_ATTR, '_instances'),
        (bp.CALL_FUNCTION, 1),
        (bp.STORE_FAST, 'instances_begin_index')
    ]

    # Add this code to the end to copy the new components added by f() to the
    # _instances list and store them in a local variable. A list containing
    # the new components will be returned.
    # Python version of postamble:
    #   loc_insts = pygmyhdl._instances[instances_begin_index:]
    #   pygmyhdl._instances = pygmyhdl._instances[:begin_begin_index]
    #   pygmyhdl._instances.append(loc_insts)
    #   return loc_insts
    postamble = [
        (bp.LOAD_GLOBAL, 'postamble_func'),
        (bp.LOAD_FAST, 'instances_begin_index'),
        (bp.LOAD_GLOBAL, 'instances'),
        (bp.CALL_FUNCTION, 0),
        (bp.CALL_FUNCTION, 2),
        (bp.STORE_FAST, 'loc_insts'),
        (bp.LOAD_FAST, 'loc_insts'),
        (bp.RETURN_VALUE, None)
    ]

    # Remove the original return value and return instruction from f().
    f_code.code.pop()
    f_code.code.pop()

    # Create new code section from preamble + original code + postamble.
    new_code = preamble
    new_code.extend(f_code.code)
    new_code.extend(postamble)
    f_code.code = new_code

    # Make a copy of the original function, replace its code section with the
    # altered code section, and return the result as the decorated function.
    return _func_copy(f, f_code.to_code())


############## Logic gate definitions. #################

def inv_g(a, o):
    def blk_func(a, o):
        @always_comb
        def logic():
            o.next = not a
        return logic
    gate_inst = blk_func(a, o)
    _instances.append(gate_inst)
    return gate_inst

def and_g(a, b, o):
    def blk_func(a, b, o):
        @always_comb
        def logic():
            o.next = a & b
        return logic
    gate_inst = blk_func(a, b, o)
    _instances.append(gate_inst)
    return gate_inst

def or_g(a, b, o):
    def blk_func(a, b, o):
        @always_comb
        def logic():
            o.next = a | b
        return logic
    gate_inst = blk_func(a, b, o)
    _instances.append(gate_inst)
    return gate_inst

def xor_g(a, b, o):
    def blk_func(a, b, o):
        @always_comb
        def logic():
            o.next = a ^ b
        return logic
    gate_inst = blk_func(a, b, o)
    _instances.append(gate_inst)
    return gate_inst
