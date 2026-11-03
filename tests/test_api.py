import json
import hub.api as api


def test_parse_upnp_discovery_result() -> None:
    first = {
        'location': 'http://192.168.1.2/root.xml',
        'control_url': 'http://192.168.1.2/upnp/control',
    }
    gateway = {
        'location': 'http://192.168.1.1/root.xml',
        'control_url': 'http://192.168.1.1:5000/ctl/IPConn',
    }
    output = json.dumps({'gateway': '192.168.1.1', 'response_count': 3, 'igds': [first, gateway]})
    selected, igds, response_count = api.parse_upnp_discovery_result(output)
    assert selected == gateway['control_url']
    assert len(igds) == 2
    assert response_count == 3
    output = json.dumps({'gateway': '192.168.1.254', 'igds': [first, gateway]})
    selected, igds, response_count = api.parse_upnp_discovery_result(output)
    assert selected == first['control_url']
    assert len(igds) == 2
    assert response_count == 2  # compatibility with clients that omit the count
    output = json.dumps({'gateway': '192.168.1.1', 'igds': []})
    assert api.parse_upnp_discovery_result(output) == ('unavailable', [], 0)
