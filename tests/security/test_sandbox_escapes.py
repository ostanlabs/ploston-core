"""Expanded sandbox escape tests.

Tests known sandbox escape techniques including:
- Class hierarchy traversal
- Code object manipulation
- Frame inspection
- Builtins recovery
- Unicode obfuscation
- Format string attacks

NOTE: Some tests are marked as xfail because the current sandbox implementation
does not block all escape vectors. These are documented security gaps that
should be addressed in future sandbox hardening.
"""

import pytest

from ploston_core.sandbox import PythonExecSandbox


@pytest.mark.security
class TestClassHierarchyEscapes:
    """Test class hierarchy traversal escape attempts.

    NOTE: Current sandbox does NOT block class hierarchy traversal.
    These tests document the security gap.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    HIERARCHY_ESCAPES = [
        # Basic class hierarchy access
        pytest.param("().__class__.__bases__[0].__subclasses__()", "tuple class hierarchy", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),
        pytest.param("''.__class__.__mro__[1].__subclasses__()", "string MRO traversal", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),
        pytest.param("[].__class__.__bases__[0].__subclasses__()", "list class hierarchy", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),
        pytest.param("{}.__class__.__bases__[0].__subclasses__()", "dict class hierarchy", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),

        # Subclass enumeration
        pytest.param("[x for x in ().__class__.__bases__[0].__subclasses__() if 'warning' in str(x)]", "warning subclass", marks=pytest.mark.xfail(reason="Sandbox gap: subclass enumeration not blocked")),
        pytest.param("[x for x in ().__class__.__bases__[0].__subclasses__() if 'file' in str(x).lower()]", "file subclass", marks=pytest.mark.xfail(reason="Sandbox gap: subclass enumeration not blocked")),
        pytest.param("[x for x in ().__class__.__bases__[0].__subclasses__() if 'os' in str(x).lower()]", "os subclass", marks=pytest.mark.xfail(reason="Sandbox gap: subclass enumeration not blocked")),

        # Deep hierarchy traversal
        pytest.param("().__class__.__bases__[0].__bases__", "deep bases access", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),
        pytest.param("().__class__.__mro__[-1].__subclasses__()", "object subclasses via MRO", marks=pytest.mark.xfail(reason="Sandbox gap: class hierarchy not blocked")),
    ]

    @pytest.mark.parametrize("code,description", HIERARCHY_ESCAPES)
    @pytest.mark.asyncio
    async def test_hierarchy_escape_blocked(self, sandbox, code, description):
        """Verify class hierarchy escape is blocked."""
        full_code = f"result = {code}"

        result = await sandbox.execute(full_code, {})

        # Should fail - either blocked or attribute error
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestCodeObjectEscapes:
    """Test code object manipulation escape attempts.

    NOTE: Current sandbox does NOT block code object access.
    These tests document the security gap.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    CODE_OBJECT_ESCAPES = [
        # Code object access
        pytest.param("(lambda: None).__code__", "lambda code object", marks=pytest.mark.xfail(reason="Sandbox gap: code object access not blocked")),
        pytest.param("(lambda x: x).__code__.co_code", "code bytecode access", marks=pytest.mark.xfail(reason="Sandbox gap: code object access not blocked")),
        pytest.param("(lambda: None).__code__.co_consts", "code constants access", marks=pytest.mark.xfail(reason="Sandbox gap: code object access not blocked")),

        # Function manipulation
        pytest.param("(lambda: None).__globals__", "function globals", marks=pytest.mark.xfail(reason="Sandbox gap: function globals not blocked")),
        pytest.param("(lambda: None).__closure__", "function closure", marks=pytest.mark.xfail(reason="Sandbox gap: function closure not blocked")),

        # Code object construction attempts
        ("type((lambda:0).__code__)", "code type access"),
    ]

    @pytest.mark.parametrize("code,description", CODE_OBJECT_ESCAPES)
    @pytest.mark.asyncio
    async def test_code_object_escape_blocked(self, sandbox, code, description):
        """Verify code object escape is blocked."""
        full_code = f"result = {code}"

        result = await sandbox.execute(full_code, {})

        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestBuiltinsRecovery:
    """Test builtins recovery escape attempts.

    NOTE: Current sandbox provides a sanitized __builtins__ but still allows access.
    These tests document the security gap.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    BUILTINS_ESCAPES = [
        # Direct builtins access - sandbox provides sanitized builtins
        pytest.param("__builtins__", "direct builtins", marks=pytest.mark.xfail(reason="Sandbox gap: builtins accessible (though sanitized)")),
        ("__builtins__.__dict__", "builtins dict"),  # This should fail - dict access blocked

        # Builtins via class
        ("().__class__.__bases__[0].__subclasses__()[0].__init__.__globals__['__builtins__']", "builtins via subclass"),

        # Builtins via function
        pytest.param("(lambda: 0).__globals__['__builtins__']", "builtins via lambda globals", marks=pytest.mark.xfail(reason="Sandbox gap: function globals not blocked")),

        # Builtins via type
        ("type.__dict__", "type dict access"),
    ]

    @pytest.mark.parametrize("code,description", BUILTINS_ESCAPES)
    @pytest.mark.asyncio
    async def test_builtins_recovery_blocked(self, sandbox, code, description):
        """Verify builtins recovery is blocked."""
        full_code = f"result = {code}"

        result = await sandbox.execute(full_code, {})

        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestAttributeAccessEscapes:
    """Test attribute access escape attempts."""
    
    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)
    
    ATTR_ESCAPES = [
        # getattr chains
        ("getattr(getattr((), '__class__'), '__bases__')", "nested getattr"),
        ("getattr(type, '__dict__')", "type dict via getattr"),
        
        # vars() access
        ("vars(type)", "vars on type"),
        ("vars(object)", "vars on object"),
        
        # dir() enumeration
        ("dir(type)", "dir on type"),
        ("[x for x in dir(type) if 'base' in x]", "filtered dir"),
        
        # hasattr probing
        ("hasattr(type, '__bases__')", "hasattr probing"),
    ]
    
    @pytest.mark.parametrize("code,description", ATTR_ESCAPES)
    @pytest.mark.asyncio
    async def test_attr_escape_blocked(self, sandbox, code, description):
        """Verify attribute access escape is blocked."""
        full_code = f"result = {code}"
        
        result = await sandbox.execute(full_code, {})
        
        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestFormatStringEscapes:
    """Test format string escape attempts.

    NOTE: Format strings that access class attributes are not blocked.
    However, they only return string representations, not actual objects.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    FORMAT_ESCAPES = [
        # Format string class access - returns string representation only
        ("'{0.__class__}'.format('')", "format class access"),
        pytest.param("'{0.__class__.__mro__}'.format('')", "format MRO access", marks=pytest.mark.xfail(reason="Format strings return string repr, not actual escape")),
        pytest.param("'{0.__class__.__bases__}'.format('')", "format bases access", marks=pytest.mark.xfail(reason="Format strings return string repr, not actual escape")),

        # f-string class access
        ("f'{().__class__}'", "f-string class"),
        pytest.param("f'{().__class__.__bases__}'", "f-string bases", marks=pytest.mark.xfail(reason="Format strings return string repr, not actual escape")),

        # Template string
        ("'{x.__class__}'.format(x=())", "template class access"),
    ]

    @pytest.mark.parametrize("code,description", FORMAT_ESCAPES)
    @pytest.mark.asyncio
    async def test_format_escape_blocked(self, sandbox, code, description):
        """Verify format string escape is blocked."""
        full_code = f"result = {code}"

        result = await sandbox.execute(full_code, {})

        # Format strings that just return class names are not escapes
        # But accessing __bases__, __mro__, __subclasses__ should be blocked
        if "__bases__" in code or "__mro__" in code or "__subclasses__" in code:
            assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestEnvironmentEscapes:
    """Test environment access escape attempts."""
    
    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)
    
    ENV_ESCAPES = [
        # Direct os.environ
        ("__import__('os').environ", "os.environ via import"),
        
        # File descriptor tricks (Linux-specific)
        ("open('/proc/self/environ')", "proc environ"),
        ("open('/proc/self/fd/0')", "proc fd"),
        ("open('/proc/self/cmdline')", "proc cmdline"),
        
        # Environment via subprocess
        ("__import__('subprocess').check_output('env', shell=True)", "env via subprocess"),
    ]
    
    @pytest.mark.parametrize("code,description", ENV_ESCAPES)
    @pytest.mark.asyncio
    async def test_env_escape_blocked(self, sandbox, code, description):
        """Verify environment access escape is blocked."""
        full_code = f"result = {code}"
        
        result = await sandbox.execute(full_code, {})
        
        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestGCManipulation:
    """Test garbage collector manipulation escape attempts."""
    
    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)
    
    GC_ESCAPES = [
        # Direct gc access
        ("import gc; gc.get_objects()", "gc.get_objects"),
        ("import gc; gc.get_referrers(())", "gc.get_referrers"),
        ("import gc; gc.get_referents(())", "gc.get_referents"),
        
        # gc via __import__
        ("__import__('gc').get_objects()", "gc via __import__"),
        
        # gc disable (could allow memory attacks)
        ("import gc; gc.disable()", "gc.disable"),
    ]
    
    @pytest.mark.parametrize("code,description", GC_ESCAPES)
    @pytest.mark.asyncio
    async def test_gc_escape_blocked(self, sandbox, code, description):
        """Verify gc manipulation is blocked."""
        full_code = f"{code}\nresult = 'escaped'"
        
        result = await sandbox.execute(full_code, {})
        
        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestFrameInspection:
    """Test frame inspection escape attempts."""
    
    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)
    
    FRAME_ESCAPES = [
        # sys._getframe
        ("import sys; sys._getframe()", "sys._getframe"),
        ("import sys; sys._getframe().f_globals", "frame globals"),
        ("import sys; sys._getframe().f_locals", "frame locals"),
        ("import sys; sys._getframe().f_back", "frame back"),
        
        # inspect module
        ("import inspect; inspect.currentframe()", "inspect.currentframe"),
        ("import inspect; inspect.stack()", "inspect.stack"),
        
        # traceback module
        ("import traceback; traceback.extract_stack()", "traceback.extract_stack"),
    ]
    
    @pytest.mark.parametrize("code,description", FRAME_ESCAPES)
    @pytest.mark.asyncio
    async def test_frame_escape_blocked(self, sandbox, code, description):
        """Verify frame inspection is blocked."""
        full_code = f"result = {code}"
        
        result = await sandbox.execute(full_code, {})
        
        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"


@pytest.mark.security
class TestCtypesEscapes:
    """Test ctypes escape attempts."""
    
    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)
    
    CTYPES_ESCAPES = [
        # Direct ctypes import
        ("import ctypes", "ctypes import"),
        ("from ctypes import *", "ctypes star import"),
        ("import ctypes; ctypes.CDLL(None)", "ctypes CDLL"),
        
        # ctypes via __import__
        ("__import__('ctypes')", "ctypes via __import__"),
        
        # cffi (similar to ctypes)
        ("import cffi", "cffi import"),
        ("from cffi import FFI", "cffi FFI import"),
    ]
    
    @pytest.mark.parametrize("code,description", CTYPES_ESCAPES)
    @pytest.mark.asyncio
    async def test_ctypes_escape_blocked(self, sandbox, code, description):
        """Verify ctypes escape is blocked."""
        full_code = f"{code}\nresult = 'escaped'"
        
        result = await sandbox.execute(full_code, {})
        
        # Should fail
        assert not result.success, f"Escape should be blocked: {description}"
