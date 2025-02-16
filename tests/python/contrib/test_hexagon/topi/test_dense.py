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
"""Test code for dense"""
import numpy as np
import pytest
import sys

import tvm
from tvm import topi
from tvm import te
from tvm.contrib.hexagon.session import Session
import tvm.topi.testing
from tvm.topi.utils import get_const_tuple

from ..conftest import requires_hexagon_toolchain

random_seed = tvm.testing.parameter(0)

use_bias = tvm.testing.parameter(True, False)

# batch_size more than 8 would break
batch_size = tvm.testing.parameter(1, 2, 8)

in_dim, out_dim = tvm.testing.parameters((1024, 1000))

in_dtype, out_dtype = tvm.testing.parameters(
    ("float32", "float32"),
    ("float16", "float32"),
    ("int8", "int32"),
)


@tvm.testing.fixture(cache_return_value=True)
def dense_ref_data(random_seed, batch_size, in_dim, out_dim, use_bias, in_dtype, out_dtype):
    np.random.seed(random_seed)

    if "float" in in_dtype:
        a_np = np.random.uniform(size=(batch_size, in_dim)).astype(in_dtype)
        b_np = np.random.uniform(size=(out_dim, in_dim)).astype(in_dtype)
        c_np = np.random.uniform(size=(out_dim,)).astype(out_dtype)
    elif in_dtype == "int8":
        a_np = np.random.randint(low=-128, high=127, size=(batch_size, in_dim)).astype(in_dtype)
        b_np = np.random.randint(low=-128, high=127, size=(out_dim, in_dim)).astype(in_dtype)
        c_np = np.random.randint(low=-128, high=127, size=(out_dim,)).astype(out_dtype)
    else:
        raise ValueError("No method to generate test data for data type '{}'".format(in_dtype))

    matmul = np.dot(a_np.astype(out_dtype), b_np.T.astype(out_dtype))

    if use_bias:
        matmul += c_np

    d_np = np.maximum(matmul, 0)
    return (a_np, b_np, c_np, d_np)


@requires_hexagon_toolchain
def test_dense(
    hexagon_session: Session,
    batch_size,
    in_dim,
    out_dim,
    use_bias,
    in_dtype,
    out_dtype,
    dense_ref_data,
):
    if in_dtype == "float16":
        pytest.xfail("float16 is not supported.")

    if "int" in in_dtype:
        tol = {"atol": 0, "rtol": 0}
    elif in_dtype == "float32":
        tol = {"rtol": 1e-5, "atol": 1e-5}

    A = te.placeholder((batch_size, in_dim), name="A", dtype=in_dtype)
    B = te.placeholder((out_dim, in_dim), name="B", dtype=in_dtype)
    C = te.placeholder((out_dim,), name="C", dtype=out_dtype)

    a_np, b_np, c_np, d_np = dense_ref_data

    fcompute = topi.nn.dense
    fschedule = topi.hexagon.schedule_dense

    target_hexagon = tvm.target.hexagon("v68")
    with tvm.target.Target(target_hexagon):
        D = fcompute(A, B, C if use_bias else None, out_dtype)
        D = topi.nn.relu(D)
        s = fschedule([D])

    func = tvm.build(
        s, [A, B, C, D], tvm.target.Target(target_hexagon, host=target_hexagon), name="dense"
    )
    mod = hexagon_session.load_module(func)

    dev = hexagon_session.device
    a = tvm.nd.array(a_np, dev)
    b = tvm.nd.array(b_np, dev)
    c = tvm.nd.array(c_np, dev)
    d = tvm.nd.array(np.zeros(get_const_tuple(D.shape), dtype=out_dtype), dev)
    mod["dense"](a, b, c, d)
    tvm.testing.assert_allclose(d.numpy(), d_np, **tol)


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv))
