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
"""Test code for matmul"""
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

dtype = tvm.testing.parameter(
    "float32",
    "float16",
)


class TestMatMulFloat:
    x_batch, y_batch, M, N, K = tvm.testing.parameters(
        (1, 1, 16, 16, 32),
        (5, 5, 16, 16, 32),
        (5, 5, 16, 20, 32),
        (30, 30, 16, 20, 32),
        # Test batch broadcasting.
        (1, 5, 16, 16, 32),
        (5, 1, 16, 16, 32),
    )

    # TODO(mehrdadh): add dynamic testing
    @requires_hexagon_toolchain
    def test_batch_matmul(self, hexagon_session: Session, x_batch, y_batch, M, N, K, dtype):
        if dtype == "float16":
            pytest.xfail("float16 is not supported.")

        x = te.placeholder((x_batch, M, K), name="x")
        y = te.placeholder((y_batch, N, K), name="y")

        def get_ref_data():
            a_np = np.random.uniform(size=(x_batch, M, K)).astype(dtype)
            b_np = np.random.uniform(size=(y_batch, N, K)).astype(dtype)
            c_np = tvm.topi.testing.batch_matmul(a_np, b_np)
            return (a_np, b_np, c_np)

        # get the test data
        a_np, b_np, c_np = get_ref_data()

        target_hexagon = tvm.target.hexagon("v68")
        with tvm.target.Target(target_hexagon):
            fcompute = topi.nn.batch_matmul
            fschedule = topi.hexagon.schedule_batch_matmul
            out = fcompute(x, y)
            s = fschedule([out])
            out_shape = out.shape

        func = tvm.build(
            s,
            [x, y, out],
            tvm.target.Target(target_hexagon, host=target_hexagon),
            name="batch_matmul",
        )
        mod = hexagon_session.load_module(func)

        dev = hexagon_session.device
        a = tvm.nd.array(a_np, dev)
        b = tvm.nd.array(b_np, dev)
        c = tvm.nd.array(np.zeros(get_const_tuple(out_shape), dtype=dtype), dev)
        mod["batch_matmul"](a, b, c)

        tvm.testing.assert_allclose(c.numpy(), c_np, rtol=1e-5)


class TestMatMulInt8:
    x_batch, y_batch, M, N, K = tvm.testing.parameters(
        (1, 1, 2, 3, 1),
        (1, 1, 16, 24, 32),
        (5, 5, 24, 16, 32),
        (30, 30, 16, 20, 32),
        (1, 5, 16, 16, 32),
        (5, 1, 16, 16, 32),
    )

    @requires_hexagon_toolchain
    def test_batch_matmul_int8(self, hexagon_session: Session, x_batch, y_batch, M, N, K):
        dtype = "int8"
        out_dtype = "int8"
        assert x_batch == y_batch or x_batch == 1 or y_batch == 1
        x = te.placeholder((x_batch, M, K), name="x", dtype=dtype)
        y = te.placeholder((y_batch, N, K), name="y", dtype=dtype)

        def get_ref_data():
            a_np = np.random.randint(low=-128, high=127, size=(x_batch, M, K)).astype(dtype)
            b_np = np.random.randint(low=-128, high=127, size=(y_batch, N, K)).astype(dtype)
            c_np = tvm.topi.testing.batch_matmul(a_np, b_np, out_dtype=out_dtype)
            return (a_np, b_np, c_np)

        # get the test data
        a_np, b_np, c_np = get_ref_data()

        target_hexagon = tvm.target.hexagon("v68")
        with tvm.target.Target(target_hexagon):
            fcompute = topi.nn.batch_matmul
            fschedule = topi.hexagon.schedule_batch_matmul
            out = fcompute(x, y)
            s = fschedule([out])

        func = tvm.build(
            s,
            [x, y, out],
            tvm.target.Target(target_hexagon, host=target_hexagon),
            name="batch_matmul_int8",
        )
        mod = hexagon_session.load_module(func)

        dev = hexagon_session.device
        a = tvm.nd.array(a_np, dev)
        b = tvm.nd.array(b_np, dev)
        c = tvm.nd.array(np.zeros(get_const_tuple(out.shape), dtype=out_dtype), dev)
        mod["batch_matmul_int8"](a, b, c)
        tvm.testing.assert_allclose(c.numpy(), c_np, rtol=1e-5)


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv))
