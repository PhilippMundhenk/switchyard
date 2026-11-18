"""The HTTP API and the static single-page interface."""

from __future__ import annotations

import ipaddress
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Config
from ..manager import Manager, NotFound
from ..nsdp import Transport
from ..nsdp import tags as T
from ..nsdp.client import AuthError, NsdpError, PasswordRequired, Unreachable
from ..nsdp.netif import list_interfaces
from ..nsdp.packet import format_mac
from ..sim import Simulator, demo_switches
from ..store import Store
from .auth import COOKIE, Auth

log = logging.getLogger(__name__)
MAC_PATH = re.compile(r"^(/api/devices/)([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})(/.*)?$")
STATIC = Path(__file__).parent / "static"

VlanMode = Literal["disabled", "port_basic", "port_advanced", "dot1q_basic", "dot1q_advanced"]
Priority = Literal["high", "medium", "normal", "low"]
Vid = int
PortMap = dict[int, int]


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    current: str = ""
    new: str = Field(min_length=8, max_length=200)


class AddBody(BaseModel):
    mac: str | None = None
    address: str | None = None
    label: str = Field("", max_length=60)
    password: str | None = None
    remember: bool = True


class UpdateBody(BaseModel):
    label: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=2000)
    address: str | None = None
    set_address: bool = False
    forget_password: bool = False


class SwitchLoginBody(BaseModel):
    password: str
    remember: bool = True


class NetworkBody(BaseModel):
    name: str | None = Field(None, max_length=20)
    dhcp: bool | None = None
    ip: str | None = None
    netmask: str | None = None
    gateway: str | None = None


class VlanModeBody(BaseModel):
    mode: VlanMode


class PortVlan(BaseModel):
    id: int = Field(ge=1, le=64)
    members: list[int]


class PortVlansBody(BaseModel):
    vlans: list[PortVlan]


class Dot1qVlan(BaseModel):
    id: Vid = Field(ge=1, le=4093)
    members: list[int]
    tagged: list[int] = []


class Dot1qBody(BaseModel):
    vlans: list[Dot1qVlan]
    pvid: PortMap = {}


class QosBody(BaseModel):
    mode: Literal["port", "dot1p"] | None = None
    priorities: dict[int, Priority] | None = None
    ingress: PortMap | None = None
    egress: PortMap | None = None
    storm_control: bool | None = None
    storm_rates: PortMap | None = None


class MirrorBody(BaseModel):
    destination: int = Field(ge=0)
    sources: list[int] = []


class IgmpBody(BaseModel):
    enabled: bool | None = None
    vlan: int = Field(1, ge=1, le=4093)
    block_unknown: bool | None = None
    validate_v3: bool | None = None


class FlagBody(BaseModel):
    enabled: bool


class CableBody(BaseModel):
    ports: list[int] = Field(min_length=1, max_length=48)


class NewSwitchPassword(BaseModel):
    new: str = Field(min_length=1, max_length=20)


class ConfirmBody(BaseModel):
    confirm: str


class RestoreBody(BaseModel):
    backup: dict
    include_network: bool = False


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, **extra}, status_code=status)


def create_app(config: Config) -> FastAPI:
    simulator = Simulator(demo_switches()) if config["demo.enabled"] else None
    auth = Auth(
        config.data_dir,
        config["auth.password"],
        config["auth.disabled"],
        config["auth.session_hours"],
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        targets = list(config["nsdp.targets"])
        client_port = config["nsdp.client_port"]
        if simulator:
            port = await simulator.start()
            targets, client_port = [f"127.0.0.1:{port}"], 0
            log.warning("demo mode: four simulated switches, the network is not used")
        transport = Transport(
            client_port, config["nsdp.device_port"], targets, config["nsdp.interfaces"]
        )
        manager = Manager(config, transport, Store(config.data_dir / "devices.json"))
        await manager.start()
        app.state.manager = manager
        log.info("NSDP on UDP %s via %s", transport.client_port, [r.name for r in transport.routes])
        try:
            yield
        finally:
            await manager.stop()
            if simulator:
                simulator.stop()

    app = FastAPI(
        title="Switchyard",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.auth = auth
    app.state.simulator = simulator
    app.state.config = config

    def manager() -> Manager:
        return app.state.manager

    # -- guards --------------------------------------------------------------

    open_paths = {"/api/login", "/api/session", "/api/health", "/health"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        match = MAC_PATH.match(path)
        if match:  # accept upper case and dashes in MAC addresses
            mac = match.group(2).lower().replace("-", ":")
            request.scope["path"] = path = f"{match.group(1)}{mac}{match.group(3) or ''}"
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and urlsplit(origin).netloc != request.headers.get("host"):
                    return error(403, "bad_origin", "Cross-site request refused.")
            if path not in open_paths and not auth.valid(request.cookies.get(COOKIE)):
                return error(401, "login_required", "Please log in.")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotFound)
    async def _not_found(request, exc):
        return error(404, "not_found", "This switch is not in the list.")

    @app.exception_handler(Unreachable)
    async def _unreachable(request, exc):
        return error(504, "unreachable", str(exc))

    @app.exception_handler(PasswordRequired)
    async def _password_required(request, exc):
        return error(409, "password_required", str(exc))

    @app.exception_handler(AuthError)
    async def _switch_auth(request, exc):
        return error(409, "switch_auth", str(exc), switch_status=exc.status)

    @app.exception_handler(NsdpError)
    async def _switch_error(request, exc):
        return error(502, "switch_error", str(exc), switch_status=exc.status, tag=exc.tag)

    @app.exception_handler(ValueError)
    async def _invalid(request, exc):
        return error(422, "invalid", str(exc))

    # -- session -------------------------------------------------------------

    api = APIRouter(prefix="/api")

    def health() -> dict:
        m = manager()
        return {
            "status": "ok",
            "version": __version__,
            "demo": simulator is not None,
            "devices": len(m.store.devices),
            "routes": [r.name for r in m.transport.routes],
        }

    api.get("/health")(health)
    app.get("/health", include_in_schema=False)(health)

    @api.get("/session")
    def session(request: Request):
        return {
            "authenticated": auth.valid(request.cookies.get(COOKIE)),
            "auth_disabled": auth.disabled,
            "demo": simulator is not None,
            "version": __version__,
        }

    @api.post("/login")
    def login(body: LoginBody, request: Request):
        try:
            token = auth.login(body.password)
        except PermissionError as exc:
            return error(
                429,
                "locked",
                f"Too many failed logins. Try again in {exc.args[0]} seconds.",
                retry_after=exc.args[0],
            )
        if token is None:
            return error(401, "bad_password", "Wrong password.")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            COOKIE,
            token,
            max_age=int(auth.session_seconds),
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @api.post("/logout")
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE)
        return response

    @api.post("/password")
    def change_password(body: PasswordBody):
        if auth.disabled:
            return error(422, "invalid", "Login is disabled in the configuration.")
        if config["auth.password"]:
            return error(
                422, "invalid", "The password is set in the configuration; change it there."
            )
        if not auth.check(body.current):
            return error(422, "invalid", "The current password is wrong.")
        auth.set_password(body.new)
        return {"ok": True, "message": "Password changed. Please log in again."}

    # -- system --------------------------------------------------------------

    @api.get("/system")
    def system():
        m = manager()
        return {
            **health(),
            "interfaces": [
                {"name": i.name, "ip": i.ip, "mac": format_mac(i.mac)} for i in list_interfaces()
            ],
            "client_port": m.transport.client_port,
            "device_port": m.transport.device_port,
            "targets": m.transport.targets,
            "last_scan": m.last_scan,
            "config_file": str(config.path),
            "password_from_config": bool(config["auth.password"]),
            "auth_disabled": auth.disabled,
        }

    @api.get("/log")
    def packet_log(after: int = 0, device: str | None = None):
        rows = [
            r
            for r in manager().transport.packet_log
            if r["id"] > after and (device is None or r["device"] == device)
        ]
        return {"entries": rows}

    @api.get("/meta")
    def meta():
        return {
            "vlan_modes": T.VLAN_MODES,
            "priorities": list(T.PRIORITIES.values()),
            "rate_codes": T.RATE_CODES,
            "link_codes": {k: v[0] for k, v in T.LINK_CODES.items()},
        }

    # -- discovery and registry ----------------------------------------------

    @api.get("/discovery")
    def discovery():
        m = manager()
        return {"last_scan": m.last_scan, "found": m.discovery()}

    @api.post("/discovery/scan")
    async def scan():
        m = manager()
        found = await m.scan()
        return {"last_scan": m.last_scan, "found": found}

    @api.get("/devices")
    def devices():
        return {"devices": manager().devices()}

    @api.post("/devices", status_code=201)
    async def add_device(body: AddBody):
        return await manager().add(body.mac, body.address, body.label, body.password, body.remember)

    @api.get("/devices/{mac}")
    def get_device(mac: str):
        m = manager()
        return m.view(m.device(mac))

    @api.patch("/devices/{mac}")
    def update_device(mac: str, body: UpdateBody):
        fields = {
            "label": body.label,
            "notes": body.notes,
            "forget_password": body.forget_password,
        }
        if body.set_address:
            fields["address"] = body.address
        return manager().update(mac, **fields)

    @api.delete("/devices/{mac}")
    def remove_device(mac: str):
        manager().remove(mac)
        return {"ok": True}

    @api.post("/devices/{mac}/login")
    async def switch_login(mac: str, body: SwitchLoginBody):
        return await manager().login(mac, body.password, body.remember)

    # -- reading -------------------------------------------------------------

    @api.get("/devices/{mac}/state")
    async def state(mac: str):
        return await manager().state(mac)

    @api.get("/devices/{mac}/ports")
    async def ports(mac: str):
        return {"ports": await manager().client(mac).read_ports()}

    # -- changing ------------------------------------------------------------

    def ok(message: str) -> dict:
        return {"ok": True, "message": message}

    @api.put("/devices/{mac}/network")
    async def set_network(mac: str, body: NetworkBody):
        for value in (body.ip, body.netmask, body.gateway):
            if value:
                ipaddress.IPv4Address(value)
        m = manager()
        await m.client(mac).set_network(body.name, body.dhcp, body.ip, body.netmask, body.gateway)
        if body.dhcp is not None and m.device(mac).get("address"):
            m.store.update(mac, address=m.client(mac).address)
        return ok("Settings saved.")

    @api.put("/devices/{mac}/vlan/mode")
    async def set_vlan_mode(mac: str, body: VlanModeBody):
        await manager().client(mac).set_vlan_mode(body.mode)
        return ok("VLAN mode changed. The switch reset its VLAN table.")

    @api.put("/devices/{mac}/vlan/port-based")
    async def set_port_vlans(mac: str, body: PortVlansBody):
        await manager().client(mac).set_port_vlans([v.model_dump() for v in body.vlans])
        return ok("VLANs saved.")

    @api.put("/devices/{mac}/vlan/dot1q")
    async def set_dot1q(mac: str, body: Dot1qBody):
        await manager().client(mac).apply_dot1q([v.model_dump() for v in body.vlans], body.pvid)
        return ok("VLANs saved.")

    @api.put("/devices/{mac}/qos")
    async def set_qos(mac: str, body: QosBody):
        for table in (body.ingress, body.egress, body.storm_rates):
            if table and any(code not in T.RATE_CODES for code in table.values()):
                raise ValueError("Unknown rate limit.")
        await manager().client(mac).set_qos(**body.model_dump())
        return ok("QoS settings saved.")

    @api.put("/devices/{mac}/mirror")
    async def set_mirror(mac: str, body: MirrorBody):
        await manager().client(mac).set_mirror(body.destination, body.sources)
        return ok("Mirroring saved.")

    @api.put("/devices/{mac}/igmp")
    async def set_igmp(mac: str, body: IgmpBody):
        await manager().client(mac).set_igmp(**body.model_dump())
        return ok("IGMP snooping saved.")

    @api.put("/devices/{mac}/loop-detection")
    async def set_loop_detection(mac: str, body: FlagBody):
        await manager().client(mac).set_loop_detection(body.enabled)
        return ok("Loop detection saved.")

    @api.post("/devices/{mac}/stats/reset")
    async def reset_stats(mac: str):
        await manager().client(mac).reset_stats()
        return ok("Counters cleared.")

    @api.post("/devices/{mac}/cable-test")
    async def cable_test(mac: str, body: CableBody):
        client = manager().client(mac)
        return {"results": [await client.cable_test(port) for port in body.ports]}

    @api.post("/devices/{mac}/password")
    async def switch_password(mac: str, body: NewSwitchPassword):
        await manager().change_password(mac, body.new)
        return ok("Switch password changed.")

    def confirmed(mac: str, body: ConfirmBody) -> None:
        if body.confirm.strip().lower() != mac:
            raise ValueError("Type the MAC address of the switch to confirm.")

    @api.post("/devices/{mac}/reboot")
    async def reboot(mac: str, body: ConfirmBody):
        confirmed(mac, body)
        await manager().client(mac).reboot()
        return ok("The switch is restarting.")

    @api.post("/devices/{mac}/factory-reset")
    async def factory_reset(mac: str, body: ConfirmBody):
        confirmed(mac, body)
        m = manager()
        await m.client(mac).factory_reset()
        m.clients.pop(mac, None)
        m.store.update(mac, password=None)
        return ok("The switch is back to factory settings. Its password is 'password' again.")

    @api.get("/devices/{mac}/backup")
    async def backup(mac: str):
        data = await manager().backup(mac)
        name = f"switchyard-{data['switch']['model']}-{mac.replace(':', '')}.json"
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @api.post("/devices/{mac}/restore")
    async def restore(mac: str, body: RestoreBody):
        steps = await manager().restore(mac, body.backup, body.include_network)
        return {"ok": all(s["ok"] for s in steps), "steps": steps}

    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app
