import http.client
import json
import threading
import time
import unittest
from unittest.mock import patch
from radiant_scope.server import Handler, Observatory, Server, parse_packet, wireless


class ParserTests(unittest.TestCase):
    def test_packet_fields_and_quoted_info(self):
        p = parse_packet('"1710000000.25"\t"192.0.2.1"\t"198.51.100.1"\t"TCP"\t"128"\t"hello ""world"""\t"-62"')
        self.assertEqual(p['length'], 128)
        self.assertEqual(p['rssi'], -62)
        self.assertEqual(p['info'], 'hello "world"')

    def test_malformed_packet(self):
        for line in ['', 'noise', 'foo\ta\tb\tTCP\t99', '100\ta\tb\tTCP\tnot-a-number']:
            self.assertIsNone(parse_packet(line))

    def test_non_wifi_packet(self):
        p = parse_packet('1710000000\t::1\t::1\tTCP\t80\tack\t')
        self.assertIsNone(p['rssi'])

    @patch('radiant_scope.server.run')
    def test_iw_telemetry(self, run):
        run.side_effect = ['type managed\nchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz', 'Connected to 02:00:00:00:00:01 (on wlan0)\n SSID: Test network\n freq: 5180\n signal: -53 dBm\n tx bitrate: 866.7 MBit/s', 'tx retries: 24\ntx failed: 1']
        data = wireless('wlan0')
        self.assertEqual((data['rssi'], data['channel'], data['ssid'], data['retries']), (-53,36,'Test network',24))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Observatory(demo=True)
        cls.server = Server(('127.0.0.1', 0), Handler)
        cls.server.app = cls.app
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None, token=None, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {}
        if token is not None:
            headers['X-Radiant-Token'] = token
        if host:
            headers['Host'] = host
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        data = response.read()
        result = response.status, dict(response.headers), data
        conn.close()
        return result

    def test_host_validation(self):
        self.assertEqual(self.request('/api/state', host='attacker.invalid')[0], 403)

    def test_mutation_requires_session_token(self):
        self.assertEqual(self.request('/api/capture/start', {})[0], 403)
        self.assertEqual(self.request('/api/capture/start', {}, token='wrong')[0], 403)

    def test_demo_capture_lifecycle(self):
        token = self.app.token
        self.assertEqual(self.request('/api/capture/start', {'filter':'tcp'}, token)[0], 200)
        self.assertTrue(self.app.snapshot()['capture']['running'])
        self.app.append_packet({'time':time.time(),'source':'192.0.2.1','destination':'198.51.100.1','protocol':'TCP','length':80,'rssi':None,'info':'test'})
        self.assertGreater(self.app.snapshot()['capture']['count'], 0)
        self.assertEqual(self.request('/api/capture/stop', {}, token)[0], 200)
        self.assertFalse(self.app.snapshot()['capture']['running'])
        self.assertGreater(self.app.snapshot()['capture']['buffered'], 0)

    def test_invalid_interface_and_filter(self):
        self.assertEqual(self.request('/api/interface', {'name':'../../etc'}, self.app.token)[0], 400)
        self.assertEqual(self.request('/api/capture/start', {'filter':'a'*513}, self.app.token)[0], 400)
        self.assertEqual(self.request('/api/capture/start', {'filter':[]}, self.app.token)[0], 400)

    def test_export_and_spreadsheet_safety(self):
        self.app.append_packet({'time':time.time(),'source':'192.0.2.1','destination':'198.51.100.1','protocol':'TCP','length':80,'rssi':None,'info':'=HYPERLINK("evil")'})
        status, headers, data = self.request('/api/export.csv')
        self.assertEqual(status, 200)
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertIn(b"'=HYPERLINK", data)
        self.assertTrue(json.loads(self.request('/api/export.json')[2])['demo'])

    def test_bounded_buffers_and_talkers(self):
        for i in range(2200):
            self.app.append_packet({'time':time.time(),'source':f'192.0.2.{i}','destination':'test','protocol':'TCP','length':80,'rssi':None,'info':'test'})
        self.assertLessEqual(len(self.app.packets),2000)
        self.assertLessEqual(len(self.app.talkers),2048)
        self.assertLessEqual(len(self.app.snapshot()['packets']),500)

    def test_static_assets_and_traversal(self):
        for path in ['/','/app.js','/style.css','/icon.svg']:
            status,headers,_ = self.request(path)
            self.assertEqual(status,200)
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../server.py')[0],404)

    def test_scan_demo(self):
        self.assertEqual(self.request('/api/scan',{},self.app.token)[0],200)
        self.assertGreater(len(self.app.snapshot()['networks']),0)

if __name__ == '__main__':
    unittest.main()
