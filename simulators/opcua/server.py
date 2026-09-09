"""OPC-UA protocol adapter: exposes a Fleet as an OPC-UA server and runs the update loop."""

import asyncio
import hmac
import os
import time
from typing import Optional

from asyncua import Node, Server, ua
from asyncua.crypto.permission_rules import User, UserRole
from kelvin.logs import logger

from fleet import Fleet, Point, initial_value
from settings import Settings

NAMESPACE_URI = "http://kelvininc.com/opcua-simulator"

_VARIANT_TYPES = {
    "float": ua.VariantType.Double,
    "int": ua.VariantType.Int64,
    "bool": ua.VariantType.Boolean,
}


class PasswordUserManager:
    """User manager accepting a single username/password pair; anonymous is rejected.

    Parameters:
        username: The accepted username.
        password: The accepted password.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def get_user(
        self,
        iserver: object,
        username: Optional[str] = None,
        password: Optional[str] = None,
        certificate: Optional[bytes] = None,
    ) -> Optional[User]:
        """Return a User for valid credentials, None (rejecting the session) otherwise."""
        # compare_digest on both fields: constant-time, and never short-circuits.
        user_ok = hmac.compare_digest((username or "").encode(), self._username.encode())
        password_ok = hmac.compare_digest((password or "").encode(), self._password.encode())
        if user_ok and password_ok:
            return User(role=UserRole.User)
        logger.warning("Rejected OPC-UA session", username=username or "<anonymous>")
        return None


class SimulatorServer:
    """Builds the OPC-UA address space from a Fleet and runs the update loop.

    Parameters:
        settings: The validated simulator configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fleet = Fleet(settings.assets, settings.simulation.seed)
        user_manager = None
        if settings.opcua.auth.enabled:
            user_manager = PasswordUserManager(
                settings.opcua.auth.username, settings.opcua.auth.password.get_secret_value()
            )
        self._server = Server(user_manager=user_manager)
        # (node, point) pairs the update loop writes each tick; static points are excluded.
        self._nodes: list[tuple[Node, Point]] = []

    async def start(self) -> None:
        """Initialize the server, build the address space, and serve forever."""
        settings = self._settings
        # Clients call GetEndpoints and reconnect to the advertised URL, so it must
        # carry a reachable hostname (the workload service name), never 0.0.0.0.
        advertised = settings.opcua.advertised_host or os.environ.get("KELVIN_WORKLOAD_NAME") or "localhost"
        await self._server.init()
        self._server.set_endpoint(f"opc.tcp://{advertised}:{settings.opcua.port}/")
        self._server.socket_address = ("0.0.0.0", settings.opcua.port)
        self._server.set_server_name("Kelvin OPC-UA Machine Simulator")
        # Unsecured transport, matching the catalog's open-by-default posture;
        # authentication (when enabled) still applies on top of it.
        self._server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        if settings.opcua.auth.enabled:
            self._server.set_security_IDs(["Username"])

        await self._build_address_space()

        logger.info(
            "Simulator ready",
            endpoint=f"opc.tcp://{advertised}:{settings.opcua.port}/",
            assets=self._fleet.asset_count,
            simulated_tags=len(self._fleet.simulated),
            auth=settings.opcua.auth.enabled,
            tick=settings.simulation.tick,
        )
        async with self._server:
            await self._update_loop()

    async def _build_address_space(self) -> None:
        """One folder per asset instance; simulated tags read-only, static tags writable."""
        idx = await self._server.register_namespace(NAMESPACE_URI)
        folders: dict[str, Node] = {}
        for point in self._fleet.simulated + self._fleet.static:
            folder = folders.get(point.asset)
            if folder is None:
                folder = await self._server.nodes.objects.add_folder(idx, point.asset)
                folders[point.asset] = folder
            await self._add_tag(idx, folder, point)

    async def _add_tag(self, idx: int, folder: Node, point: Point) -> None:
        spec = point.spec
        node_id = ua.NodeId(point.point_id, idx)
        node = await folder.add_variable(node_id, point.tag, initial_value(spec), varianttype=_VARIANT_TYPES[spec.type])

        description = spec.description or point.tag
        if spec.unit:
            description = f"{description} ({spec.unit})"
            await node.add_property(idx, "EngineeringUnits", spec.unit)
        await node.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(ua.LocalizedText(description), ua.VariantType.LocalizedText)),
        )

        if spec.writable:
            await node.set_writable()
        else:
            self._nodes.append((node, point))

    async def _update_loop(self) -> None:
        """Write fresh values to every simulated tag once per tick, forever."""
        tick = self._settings.simulation.tick
        start = time.monotonic()
        try:
            while True:
                t = time.monotonic() - start
                for node, point in self._nodes:
                    assert point.simulator is not None  # by construction
                    await node.write_value(point.simulator.value(t))
                await asyncio.sleep(tick)
        except Exception:
            # Fail fast (the platform restarts the workload), but say why first.
            logger.exception("Update loop failed; shutting down")
            raise
