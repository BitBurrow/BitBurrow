import asyncio
import logging
import importlib.metadata
import miniupnpc
import os
import pwd
import tempfile
import hub.db as db
import hub.net as net
import hub.util as util

Berror = util.Berror
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


async def wait_for_capture_ready(
    pcap_path: str, task: asyncio.Task[str], timeout: float = 15.0
) -> None:
    """Wait until tcpdump has written the pcap global header."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if task.done():
            await task
            raise Berror("B70180 tcpdump exited before capture became ready")
        try:
            if os.path.getsize(pcap_path) >= 24:
                return
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.05)
    raise Berror(f"B70181 tcpdump did not become ready within {timeout:g} seconds")


async def upnpc_outbound_discovery_packets(interface: str = 'any') -> bytes:
    """Using tcpdump, capture UPnP discovery (miniupnpc 2.3.3 should be 4 packets, 783 bytes)"""

    def do_upnp_discovery() -> int:
        """Return the number of UPnP devices discovered."""
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 2000  # max time in ms to wait for UPnP response
        try:
            discovered_count = upnp.discover()
        except Exception as e:
            if e.args == ('Success',):
                return 0  # miniupnpc quirk raises Exception('Success') for 0 devices
            raise Berror(f"B79051 UPnP discovery failed: {e!r}")
        if not isinstance(discovered_count, int):
            raise Berror(f"B79052 invalid UPnP discovery result: {discovered_count!r}")
        return discovered_count

    os_user = pwd.getpwuid(os.getuid()).pw_name
    with tempfile.TemporaryDirectory() as pcap_dir:
        pcap_path = os.path.join(pcap_dir, 'discovery.pcap')
        args = [
            'sudo',
            'tcpdump',
            f'--relinquish-privileges={os_user}',  # make temp file readable by os_user
            f'--interface={interface}',
            '-n',
            '--packet-buffered',  # write packets directly to the file
            '-w',
            pcap_path,
            'udp and dst port 1900 and (host 239.255.255.250 or ip6 multicast)',
        ]
        stop_event = asyncio.Event()
        tcpdump_task = asyncio.create_task(
            net.run_external_until_event(args, stop_event, stop_delay=1.0)
        )
        discovery_task: asyncio.Task[int] | None = None
        primary_e: BaseException | None = None
        try:
            await wait_for_capture_ready(pcap_path, tcpdump_task)
            discovery_task = asyncio.create_task(asyncio.to_thread(do_upnp_discovery))
            try:
                devices_found = await asyncio.shield(discovery_task)
            except asyncio.CancelledError:
                await discovery_task
                raise
            if devices_found != 0:
                logger.warning(f"B59401 found {devices_found} UPnP devices on hub (should be 0)")
        except BaseException as e:
            primary_e = e
            raise
        finally:
            stop_event.set()
            try:
                await tcpdump_task
            except BaseException as cleanup_e:
                if primary_e is None:
                    raise
                logger.warning(f"B70165 cleanup failed: {primary_e!r}: {cleanup_e!r}")
        try:
            with open(pcap_path, 'rb') as pcap_file:
                pcap = pcap_file.read()
                pcap_len = len(pcap)
                if pcap_len < 50 or pcap_len > 3000:
                    raise Berror(f"B70160 invalid packet capture size: {pcap_len}")
                else:
                    logger.info(f"B25443 UPnP capture successful; pcap file is {pcap_len} bytes")
        except OSError as e:
            raise Berror(f"B70166 could not read pcap file: {e!r}")
        return pcap


async def check_for_new_upnpc_client() -> None:
    path = 'upnp_discovery_pcap'
    try:
        upnp_client_version = importlib.metadata.version('miniupnpc')
        if db.get_blob(path, upnp_client_version) is not None:  # same client version
            return
        logger.debug(f"B26320 gathering UPnP discovery packets, UPnP library {upnp_client_version}")
        pcap = await upnpc_outbound_discovery_packets()
        try:
            db.set_blob(path, pcap, upnp_client_version)
        except Berror as e:
            if db.get_blob(path, upnp_client_version) is None:
                raise
            logger.info(f"B70167 UPnP worker conflict: {upnp_client_version}, {e!r}")
    except (Berror, importlib.metadata.PackageNotFoundError, OSError) as e:
        logger.warning(str(e))
