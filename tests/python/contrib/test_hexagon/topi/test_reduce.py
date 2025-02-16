# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Test code for reduce"""
import numpy as np
import pytest
import sys

import tvm
from tvm import topi
from tvm import te
from tvm.contrib.hexagon.session import Session
import tvm.topi.testing

from ..conftest import requires_hexagon_toolchain


in_shape, axis, keepdims, reduce_type, dtype = tvm.testing.parameters(
    ((32,), 0, False, "argmax", "float32"),
    ((32, 24, 32, 24), (1, 2, 3), True, "sum", "float32"),
    ((2, 3), None, True, "all", "bool"),
    ((32, 24 * 32 * 24), (1,), False, "max", "float32"),
    ((32, 128, 24), None, True, "sum", "float32"),
    ((32, 128, 24), None, True, "all", "bool"),
    ((32, 24, 32, 24), (0, 2), False, "min", "float32"),
    ((32, 128), 1, True, "argmax", "float32"),
    ((32, 24, 32, 24), 2, False, "argmin", "float32"),
    ((31, 21, 15), None, True, "argmax", "float32"),
    ((31, 21, 15), None, False, "sum", "float32"),
    ((2, 3), None, True, "any", "bool"),
    ((32, 128, 24), None, True, "any", "bool"),
    ((1, 4, 7), 1, True, "any", "bool"),
    ((32, 24, 32, 24), 2, False, "any", "bool"),
)


def _my_npy_argmax(arr, axis, keepdims):
    if not keepdims:
        return arr.argmax(axis=axis)
    else:
        if axis is None:
            out_shape = [1 for _ in arr.shape]
        else:
            out_shape = list(arr.shape)
            out_shape[axis] = 1

        return arr.argmax(axis=axis).reshape(out_shape)


def _my_npy_argmin(arr, axis, keepdims):
    if not keepdims:
        return arr.argmin(axis=axis)
    else:
        if axis is None:
            out_shape = [1 for _ in arr.shape]
        else:
            out_shape = list(arr.shape)
            out_shape[axis] = 1
        return arr.argmin(axis=axis).reshape(out_shape)


@tvm.testing.fixture(cache_return_value=True)
def ref_data(in_shape, axis, keepdims, reduce_type, dtype):
    # Test
    if dtype == "bool":
        in_npy_map = in_npy = np.random.choice([True, False], size=in_shape)
    else:
        in_npy = np.random.uniform(-1, 1, size=in_shape).astype(dtype)
        in_npy_map = np.sqrt(np.exp(in_npy)).astype(dtype)

    if reduce_type == "sum":
        out_npy = in_npy_map.sum(axis=axis, keepdims=keepdims)
    elif reduce_type == "all" and dtype == "bool":
        out_npy = in_npy_map.all(axis=axis, keepdims=keepdims)
    elif reduce_type == "any" and dtype == "bool":
        out_npy = in_npy_map.any(axis=axis, keepdims=keepdims)
    elif reduce_type == "max":
        out_npy = in_npy_map.max(axis=axis, keepdims=keepdims)
    elif reduce_type == "min":
        out_npy = in_npy_map.min(axis=axis, keepdims=keepdims)
    elif reduce_type == "argmax":
        out_npy = _my_npy_argmax(in_npy_map, axis=axis, keepdims=keepdims)
    elif reduce_type == "argmin":
        out_npy = _my_npy_argmin(in_npy_map, axis=axis, keepdims=keepdims)
    else:
        raise NotImplementedError

    return in_npy, in_npy_map, out_npy


@requires_hexagon_toolchain
def test_reduce_map(
    hexagon_session: Session, ref_data, in_shape, axis, keepdims, reduce_type, dtype
):
    in_npy, in_npy_map, out_npy = ref_data

    # Build the logic and compile the function
    A = te.placeholder(shape=in_shape, name="A", dtype=dtype)
    A1 = topi.sqrt(topi.exp(A))
    out_dtype = dtype
    if reduce_type == "sum":
        B = topi.sum(A1, axis=axis, keepdims=keepdims)
    elif reduce_type == "all":
        B = topi.all(A, axis=axis, keepdims=keepdims)
    elif reduce_type == "any":
        B = topi.any(A, axis=axis, keepdims=keepdims)
    elif reduce_type == "max":
        B = topi.max(A1, axis=axis, keepdims=keepdims)
    elif reduce_type == "min":
        B = topi.min(A1, axis=axis, keepdims=keepdims)
    elif reduce_type == "argmax":
        B = topi.argmax(A1, axis=axis, keepdims=keepdims)
        out_dtype = "int32"
    elif reduce_type == "argmin":
        B = topi.argmin(A1, axis=axis, keepdims=keepdims)
        out_dtype = "int32"
    else:
        raise NotImplementedError

    target_hexagon = tvm.target.hexagon("v68")
    with tvm.target.Target(target_hexagon):
        fschedule = topi.hexagon.schedule_reduce
        s = fschedule(B)

    func = tvm.build(
        s, [A, B], tvm.target.Target(target_hexagon, host=target_hexagon), name=reduce_type
    )
    mod = hexagon_session.load_module(func)

    dev = hexagon_session.device
    data_tvm = tvm.nd.array(in_npy, device=dev)
    out_tvm = tvm.nd.empty(shape=out_npy.shape, device=dev, dtype=out_dtype)

    mod[reduce_type](data_tvm, out_tvm)

    if reduce_type == "argmax" or reduce_type == "argmin":
        out_tvm_indices = out_tvm.numpy()
        if keepdims:
            out_tvm_indices = np.take(out_tvm_indices, indices=0, axis=axis)
        if axis is None:
            out_tvm_val = in_npy_map.ravel()[out_tvm_indices]
        else:
            other_indices = tuple(np.indices(in_shape[0:axis] + in_shape[(axis + 1) :]))
            sel_indices = other_indices[0:axis] + (out_tvm_indices,) + other_indices[axis:]
            out_tvm_val = in_npy_map[sel_indices]
        if reduce_type == "argmax":
            tvm.testing.assert_allclose(out_tvm_val, in_npy_map.max(axis=axis), 1e-3, 1e-3)
        elif reduce_type == "argmin":
            tvm.testing.assert_allclose(out_tvm_val, in_npy_map.min(axis=axis), 1e-3, 1e-3)
    else:
        tvm.testing.assert_allclose(out_tvm.numpy(), out_npy, 1e-3, 1e-3)


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv))
