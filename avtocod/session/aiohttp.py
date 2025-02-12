# source:
# https://github.com/aiogram/aiogram/blob/c7058584219a2138bab44b7760604bf62783aaf5/aiogram/client/session/aiohttp.py

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type, Union, cast

from aiohttp import BasicAuth, ClientError, ClientSession, TCPConnector

from avtocod.exceptions import NetworkError
from avtocod.methods.base import AvtocodMethod, AvtocodType
from avtocod.session.base import BaseSession
from avtocod.types.base import UNSET

_ProxyBasic = Union[str, Tuple[str, BasicAuth]]
_ProxyChain = Iterable[_ProxyBasic]
_ProxyType = Union[_ProxyChain, _ProxyBasic]


def _retrieve_basic(basic: _ProxyBasic) -> Dict[str, Any]:
    from aiohttp_socks.utils import parse_proxy_url  # type: ignore

    proxy_auth: Optional[BasicAuth] = None

    if isinstance(basic, str):
        proxy_url = basic
    else:
        proxy_url, proxy_auth = basic

    proxy_type, host, port, username, password = parse_proxy_url(proxy_url)
    if isinstance(proxy_auth, BasicAuth):
        username = proxy_auth.login
        password = proxy_auth.password

    return dict(
        proxy_type=proxy_type,
        host=host,
        port=port,
        username=username,
        password=password,
        rdns=True,
    )


def _prepare_connector(
    chain_or_plain: _ProxyType,
) -> Tuple[Type[TCPConnector], Dict[str, Any]]:
    from aiohttp_socks import ChainProxyConnector, ProxyConnector, ProxyInfo  # type: ignore

    # since tuple is Iterable(compatible with _ProxyChain) object, we assume that
    # user wants chained proxies if tuple is a pair of string(url) and BasicAuth
    if isinstance(chain_or_plain, str) or (
        isinstance(chain_or_plain, tuple) and len(chain_or_plain) == 2
    ):
        chain_or_plain = cast(_ProxyBasic, chain_or_plain)
        return ProxyConnector, _retrieve_basic(chain_or_plain)

    chain_or_plain = cast(_ProxyChain, chain_or_plain)
    infos: List[ProxyInfo] = []
    for basic in chain_or_plain:
        infos.append(ProxyInfo(**_retrieve_basic(basic)))

    return ChainProxyConnector, dict(proxy_infos=infos)


class AiohttpSession(BaseSession):
    def __init__(self, proxy: Optional[_ProxyType] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._session: Optional[ClientSession] = None
        self._connector_type: Type[TCPConnector] = TCPConnector
        self._connector_init: Dict[str, Any] = {}
        self._should_reset_connector = True  # flag determines connector state
        self._proxy: Optional[_ProxyType] = None

        if proxy is not None:
            try:
                self._setup_proxy_connector(proxy)
            except ImportError as exc:
                raise RuntimeError(
                    "In order to use aiohttp client for proxy requests, install "
                    "https://pypi.org/project/aiohttp-socks/"
                ) from exc

    def _setup_proxy_connector(self, proxy: _ProxyType) -> None:
        self._connector_type, self._connector_init = _prepare_connector(proxy)
        self._proxy = proxy

    @property
    def proxy(self) -> Optional[_ProxyType]:
        return self._proxy

    @proxy.setter
    def proxy(self, proxy: _ProxyType) -> None:
        self._setup_proxy_connector(proxy)
        self._should_reset_connector = True

    async def create_session(self) -> ClientSession:
        if self._should_reset_connector:
            await self.close()

        if self._session is None or self._session.closed:
            self._session = ClientSession(connector=self._connector_type(**self._connector_init))
            self._should_reset_connector = False

        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def build_data(self, request: Dict[str, Any]) -> str:
        data = {}
        for key, value in request.items():
            if value is None or value is UNSET:
                continue
            data[key] = self.prepare_value(value)
        return self.json_dumps(data)

    async def _make_request(
        self,
        url: str,
        method: AvtocodMethod[AvtocodType],
        timeout: Optional[int] = None,
    ) -> AvtocodType:
        session = await self.create_session()
        data = self.build_data(method.build_request().dict())
        try:
            async with session.post(
                url,
                headers=self.headers,
                data=data,
                timeout=self.timeout if timeout is None else timeout,
            ) as response:
                raw_response = await response.text()
                parsed_data = self.check_response(method, response.content_type, raw_response)
                return cast(AvtocodType, parsed_data.result)
        except asyncio.TimeoutError:
            raise NetworkError("Request timeout error")
        except ClientError as e:
            raise NetworkError(f"aiohttp client throws an error: {e.__class__.__name__}: {e}")

    async def __aenter__(self) -> AiohttpSession:
        await self.create_session()
        return self
