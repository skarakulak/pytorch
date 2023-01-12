# Owner(s): ["oncall: jit"]

import torch
from torch.testing._internal.jit_utils import JitTestCase
from torch.testing._internal.jit_utils import make_global
from typing import List, Optional
from torch import Tensor
from torch.awaits import Await

@torch.jit.script
class CFX(object):
    def __init__(self, a: Tensor, b: Tensor):
        self.a = a
        self.b = b

    def ma(self) -> Tensor:
        return self.a

    @torch.jit.unused
    # Even for jit.unused we need to be decl-scriptable, as jit adds stub with the same decl. If to add fx types here - during decl resolve jit will try to compile it and fail.
    def __fx_create_arg__(self, tracer):
        return tracer.create_node(
            "call_function",
            CFX,
            args=(tracer.create_arg(self.a),tracer.create_arg(self.b)),
            kwargs={},
        )

def cfx_delayed(c: CFX) -> Tensor:
    return 2 * (c.ma() + 1)

@torch.fx.wrap
def cfx_wrapped(c: CFX) -> Await[Tensor]:
    return torch.jit.awaitable(cfx_delayed, c)


class TestAwait(JitTestCase):
    def test_await_python(self):
        def foo(x: int) -> int:
            return x + 13
        aw: Await[int] = torch.jit.awaitable(foo, 13)
        self.assertTrue(aw.fn()(*aw.args()) == torch.jit.awaitable_wait(aw))
        nw = torch.jit.awaitable_nowait(33)
        self.assertTrue(nw.is_nowait())
        self.assertTrue(nw.args() == (33,))

    def test_await_type_python(self):
        def foo() -> Tensor:
            return torch.randn()
        awaits = torch.jit.annotate(List[Await[Tensor]], [])
        awaits.append(torch.jit.awaitable(foo))

    def test_script(self):
        def delayed(z: int) -> int:
            return z + 3

        @torch.jit.script
        def fn(x: Tensor):
            aw: Await[int] = torch.jit.awaitable(delayed, 99)
            a = torch.eye(2)
            b = torch.jit.awaitable_wait(aw)
            return a + b + x

        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_nowait(self):
        @torch.jit.script
        def fn(x: Tensor):
            aw = torch.jit.awaitable_nowait(13)
            a = torch.eye(2)
            b = torch.jit.awaitable_wait(aw)
            return a + b + x

        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_nowait_class(self):
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self._a = a
                self._b = b

            def a(self) -> Tensor:
                return self._a

        @torch.jit.script
        def fn(x: Tensor):
            aw = torch.jit.awaitable_nowait(C(torch.zeros(2), torch.ones(2)))
            _a = torch.eye(2)
            c = torch.jit.awaitable_wait(aw)
            return _a + c.a() + x

        make_global(C)
        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))


    def test_await_class_arg(self):

        @torch.jit.script
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self.__a = a
                self.__b = b

            def a(self) -> Tensor:
                return self.__a

        make_global(C)
        @torch.jit.script
        def delayed(c: C) -> Tensor:
            return c.a()

        @torch.jit.script
        def fn(x: Tensor):
            c = C(torch.zeros(2), torch.ones(2))
            aw = torch.jit.awaitable(delayed, c)
            _a = torch.eye(2)
            c2_t = torch.jit.awaitable_wait(aw)
            return _a + c2_t + x
        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_awaitable_to_await(self):
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self._a = a
                self._b = b


        make_global(C)
        # Can not stay in the class as Jit does not support Recursive annotations
        # (self in wait_impl can not be annotated as C as C is not defined by this time)
        def C_wait_impl(self: C):
                return self._a + self._b

        @torch.jit.script
        def fn(x: Tensor):
            aw = torch.jit.awaitable(C_wait_impl, C(torch.zeros(2), torch.ones(2)))
            _a = torch.eye(2)
            c_wait_impl_res = torch.jit.awaitable_wait(aw)
            return _a + c_wait_impl_res + x

        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_await_class_return(self):
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self._a = a
                self._b = b


        make_global(C)
        # Can not stay in the class as Jit does not support Recursive annotations
        # (self in wait_impl can not be annotated as C as C is not defined by this time)
        def C_wait_impl(self: C) -> C:
                return C(self._a * 2, self._b * 3)

        def fn_arg_C(x: C) -> Tensor:
          return x._a + x._b

        @torch.jit.script
        def fn(x: Tensor):
            aw: Await[C] = torch.jit.awaitable(C_wait_impl, C(x, x))
            _a = torch.eye(2)
            y = fn_arg_C(torch.jit.awaitable_wait(aw))
            return _a + y + x

        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))
        self.assertGraphContainsExactly(sm.graph, kind='aten::awaitable_wait', num_kind_nodes=1)

    def test_await_getattr_implicit_convertion(self):
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self._a = a
                self._b = b
            def b(self):
                return self._b


        make_global(C)
        # Can not stay in the class as Jit does not support Recursive annotations
        # (self in wait_impl can not be annotated as C as C is not defined by this time)
        def C_wait_impl(self: C) -> C:
                return C(self._a * 2, self._b * 3)

        def fn_arg_C(x: C) -> Tensor:
          return x._a + x._b

        @torch.jit.script
        def fn(x: Tensor):
            aw: Await[C] = torch.jit.awaitable(C_wait_impl, C(x, x))
            _a = torch.eye(2)
            ai = aw._a
            awb = aw.b()
            c = C(2*x, 2*x)
            return _a + ai + x + c._a + c.b()

        inp = torch.zeros(2)

        sm = torch.jit.script(fn)
        out = fn(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))
        self.assertGraphContainsExactly(sm.graph, kind='aten::awaitable_wait', num_kind_nodes=2)

    def test_await_nested(self):

        @torch.jit.script
        class C(object):
            def __init__(self, a: Tensor, b: Tensor):
                self.__a = a
                self.__b = b

            def a(self) -> Tensor:
                return self.__a

        make_global(C)

        @torch.jit.script
        def delayed(c: C) -> Await[Tensor]:
            return torch.jit.awaitable_nowait(c.a())

        @torch.jit.script
        def fn(x: Tensor) -> Await[Await[Tensor]]:
            return torch.jit.awaitable(delayed, C(x, x))

        @torch.jit.script
        def main(x: Tensor) -> Tensor:
            awaw = fn(x)
            return torch.jit.awaitable_wait(torch.jit.awaitable_wait(awaw))

        inp = torch.zeros(2)

        sm = torch.jit.script(main)
        out = main(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_eager_await_non_scriptable(self):
        # Tree type can not be compiled (Recursive type)
        class Tree(object):
            def __init__(self, v):
                self.parent = torch.jit.annotate(Optional[Tree], None)
                self.v = v
        make_global(Tree)

        def delayed(t: Tree):
            t.v = t.v + 1
            return t

        aw = torch.jit.awaitable(delayed, Tree(2))
        t = torch.jit.awaitable_wait(aw)
        self.assertTrue(t.v == 3)

    def test_await_fx(self):
        def delayed(x: Tensor) -> Tensor:
            return 2 * (x + 1)

        class M(torch.nn.Module):
            def __init__(self):
                super().__init__()

            def process(self, aw: Await[Tensor]):
                return torch.jit.awaitable_wait(aw)

            def forward(self, x: Tensor):
                aw = torch.jit.awaitable(delayed, x)
                y = 3 * x
                r = self.process(aw)
                return r + y
        m = M()
        tracer = torch.fx.Tracer()
        g = tracer.trace(m)

    def test_await_isinstance(self):
        def delayed(x: Tensor) -> Tensor:
            return 2 * (x + 1)

        @torch.jit.script
        def main(x: Tensor) -> Tensor:
            aw = torch.jit.awaitable(delayed, x)
            assert isinstance(aw, torch.jit.Await)
            return torch.jit.awaitable_wait(aw)

        inp = torch.zeros(2)

        sm = torch.jit.script(main)
        out = main(inp)
        script_out = sm(inp)
        self.assertTrue(torch.allclose(script_out, out))

    def test_await_eager_lazy(self):
        def delayed(x: Tensor) -> Tensor:
            return 2 * (x + 1)
        t = torch.ones(2, dtype=torch.int64)
        aw = torch.jit.awaitable(delayed, t)
        self.assertTrue(isinstance(aw, torch._C.Await))
        self.assertTrue(t.dtype == aw.dtype)

    def test_await_out_of_interpreter(self):
        def delayed(x: Tensor) -> Tensor:
            return 2 * (x + 1)

        @torch.jit.script
        def main(x: Tensor) -> Await[Tensor]:
            aw = torch.jit.awaitable(delayed, x)
            assert isinstance(aw, torch.jit.Await)
            return aw

        inp = torch.zeros(2)

        sm = torch.jit.script(main)
        out_aw = main(inp)
        out = torch.jit.awaitable_wait(out_aw)

        script_out_aw = sm(inp)
        script_out = torch.jit.awaitable_wait(script_out_aw)

        self.assertTrue(torch.allclose(script_out, out))

    def test_jit_trace(self):
        def gap(x: Tensor):
            return torch.relu(x) + torch.sin(x)

        def delayed(x: Tensor) -> Tensor:
            return 2 * (torch.cos(x) + 1)

        def main(x: Tensor, y: Tensor) -> Tensor:
            aw = torch.jit.awaitable(delayed, x)

            assert isinstance(aw, torch.Await)
            z = gap(y)
            k = torch.jit.awaitable_wait(aw)
            return y + k

        inp = torch.randn(2)
        tm = torch.jit.trace(main, (inp,inp))
        inp_check = torch.ones(2)
        self.assertEqual(main(inp_check, inp_check), tm(inp_check, inp_check))

    def test_await_fx(self):
        class M(torch.nn.Module):
            def __init__(self):
                super().__init__()

            def process(self, aw: Await[Tensor]):
                return torch.jit.awaitable_wait(aw)

            def forward(self, x: Tensor, y: Tensor):
                aw = cfx_wrapped(CFX(x, y))
                z = torch.sin(x)
                r = self.process(aw)
                return r + z

        m = M()
        tracer = torch.fx.Tracer()
        g = tracer.trace(m)
        gm = torch.fx.GraphModule(tracer.root, g)
        sm = torch.jit.script(gm)
        inp = torch.randn(2)
        self.assertEqual(m(inp, inp), sm(inp, inp))
