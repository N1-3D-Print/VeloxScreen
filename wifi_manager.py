"""NetworkManager-backed Wi-Fi functions for VeloxScreen.

No shell expansion, no sudo escalation. Wi-Fi secrets are provided through an
owner-only temporary password file for nmcli and are never logged or saved in
VeloxScreen configuration. NetworkManager owns the connection profiles.
"""
import os
import shutil
import subprocess
import tempfile


def parse_fields(line):
    """Parse nmcli -t escaped colon-delimited fields, including literal SSID colons."""
    fields, buf, escaped = [], [], False
    for char in line:
        if escaped:
            buf.append(char)
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(''.join(buf)); buf = []
        else:
            buf.append(char)
    if escaped:
        buf.append('\\')
    fields.append(''.join(buf))
    return fields


def command(args, timeout=15):
    if not shutil.which('nmcli'):
        raise RuntimeError('NetworkManager / nmcli is not installed. Install network-manager first.')
    p = subprocess.run(['nmcli', '--terse', '--escape', 'yes'] + list(args),
                       capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'NetworkManager command failed').strip()[:360])
    return p.stdout


def network_info():
    devices = command(['-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device', 'status'])
    wifi_iface = None
    for line in devices.splitlines():
        cols = parse_fields(line)
        if len(cols) >= 3 and cols[1] == 'wifi' and cols[2] != 'unmanaged':
            wifi_iface = cols[0]
            if cols[2] == 'connected':
                break
    if not wifi_iface:
        raise RuntimeError('No Wi-Fi device managed by NetworkManager was found.')
    aps = command(['-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list',
                   '--rescan', 'yes', 'ifname', wifi_iface], timeout=20)
    entries = {}
    for line in aps.splitlines():
        cols = parse_fields(line)
        if len(cols) != 4 or not cols[1]:
            continue
        active, ssid, signal, security = cols
        if ssid == '--':
            continue
        try: strength = int(signal)
        except ValueError: strength = 0
        row = {'ssid': ssid, 'signal': strength, 'security': security,
               'active': active.strip() == '*'}
        if ssid not in entries or row['active'] or row['signal'] > entries[ssid]['signal']:
            entries[ssid] = row
    address_lines = command(['-g', 'IP4.ADDRESS', 'device', 'show', wifi_iface])
    addresses = [x.strip() for x in address_lines.splitlines() if x.strip()]
    return {'iface': wifi_iface, 'networks': sorted(entries.values(),
             key=lambda n: (not n['active'], -n['signal'], n['ssid'].casefold())),
            'ip': addresses[0] if addresses else 'Not assigned'}


def connect_wifi(iface, ssid, password, protected):
    """Connect using a saved NM profile, an open AP, or WPA-Personal PSK.

    Password is passed via nmcli's passwd-file, NOT argv (visible to other users
    via ps). Newly created profiles are cleaned up if activation fails.
    """
    if not iface or not ssid or '\n' in ssid or '\r' in ssid:
        raise ValueError('Invalid Wi-Fi network or interface.')
    # Empty password: allow NetworkManager to activate a previously saved profile.
    if not password:
        command(['--wait', '40', 'device', 'wifi', 'connect', ssid,
                 'ifname', iface], timeout=48)
        return 'Connected to ' + ssid
    if not protected:
        raise ValueError('This network is open; connect without a password.')
    if '\n' in password or '\r' in password:
        raise ValueError('Wi-Fi password contains an invalid line break.')
    # Only WPA/WPA2 personal networks are supported by the new-profile flow.
    # WPA3-only and enterprise networks may require external setup.
    profile = 'VeloxScreen - ' + ssid
    # Names can collide with existing profiles. UUID prevents deleting others.
    import uuid
    profile_id = str(uuid.uuid4())
    command(['connection', 'add', 'type', 'wifi', 'ifname', iface,
             'con-name', profile, 'connection.uuid', profile_id,
             '802-11-wireless.ssid', ssid,
             '802-11-wireless-security.key-mgmt', 'wpa-psk',
             'connection.autoconnect', 'no'], timeout=15)
    pathname = None
    try:
        fd, pathname = tempfile.mkstemp(prefix='veloxscreen-wifi-', suffix='.secret')
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write('802-11-wireless-security.psk:' + password + '\n')
            command(['--wait', '45', 'connection', 'up', 'uuid', profile_id,
                     'ifname', iface, 'passwd-file', pathname], timeout=53)
            command(['connection', 'modify', 'uuid', profile_id,
                     'connection.autoconnect', 'yes'])
        finally:
            if pathname and os.path.exists(pathname):
                os.unlink(pathname)
    except Exception:
        try: command(['connection', 'delete', 'uuid', profile_id])
        except Exception: pass
        raise
    return 'Connected to ' + ssid
