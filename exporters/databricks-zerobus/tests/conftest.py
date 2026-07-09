"""Make the test suite runnable without the real Zerobus wheel.

`databricks-zerobus-ingest-sdk` is a Rust/maturin-built wheel that ships in the deployment
image but often can't build in a CI/dev sandbox. The unit tests mock the SDK anyway, so when
the real package isn't importable we install a minimal fake `zerobus` package into sys.modules
BEFORE the test modules import `writer`. When the real package IS present, we use it untouched.
"""
import sys
import types


def _install_fake_zerobus() -> None:
    try:
        import zerobus  # noqa: F401 ; real package present, nothing to stub
        return
    except ImportError:
        pass

    zerobus = types.ModuleType("zerobus")
    sdk = types.ModuleType("zerobus.sdk")
    aio = types.ModuleType("zerobus.sdk.aio")
    zerobus_sdk = types.ModuleType("zerobus.sdk.aio.zerobus_sdk")
    shared = types.ModuleType("zerobus.sdk.shared")

    class ZerobusSdk:           # placeholder; tests monkeypatch writer.ZerobusSdk with an async fake
        def __init__(self, *a, **k) -> None: ...

    # Mirror the real SDK's exception hierarchy: NonRetriable ⊂ Zerobus ⊂ Exception.
    class ZerobusException(Exception): ...

    class NonRetriableException(ZerobusException): ...

    class RecordType:
        JSON = "JSON"

    class StreamConfigurationOptions:
        def __init__(self, *a, **k) -> None: ...

    class TableProperties:
        def __init__(self, *a, **k) -> None: ...

    aio.ZerobusSdk = ZerobusSdk
    zerobus_sdk.ZerobusException = ZerobusException
    zerobus_sdk.NonRetriableException = NonRetriableException
    shared.RecordType = RecordType
    shared.StreamConfigurationOptions = StreamConfigurationOptions
    shared.TableProperties = TableProperties
    aio.zerobus_sdk = zerobus_sdk
    sdk.aio, sdk.shared, zerobus.sdk = aio, shared, sdk

    sys.modules.update({
        "zerobus": zerobus, "zerobus.sdk": sdk,
        "zerobus.sdk.aio": aio, "zerobus.sdk.aio.zerobus_sdk": zerobus_sdk,
        "zerobus.sdk.shared": shared,
    })


_install_fake_zerobus()
