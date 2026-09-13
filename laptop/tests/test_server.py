import json
import threading
import unittest
from http.client import HTTPConnection

from server import LanLinkState, create_server


class LanLinkServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = LanLinkState()
        cls.server = create_server(host="127.0.0.1", port=0, state=cls.state)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.state.clear_messages()

    def request(self, method, path, payload=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, data

    def test_info_contains_local_pairing_code(self):
        status, data = self.request("GET", "/api/info")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["pair_code"], self.state.pair_code)
        self.assertIn("port", data)

    def test_pair_rejects_wrong_code_and_returns_token_for_correct_code(self):
        status, data = self.request("POST", "/api/pair", {"code": "111111"})
        self.assertEqual(status, 401)
        self.assertFalse(data["ok"])

        status, data = self.request("POST", "/api/pair", {"code": self.state.pair_code})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["token"], self.state.session_token)

    def test_messages_round_trip_and_clear(self):
        status, pair = self.request("POST", "/api/pair", {"code": self.state.pair_code})
        self.assertEqual(status, 200)

        status, sent = self.request(
            "POST", "/api/messages", {"token": pair["token"], "text": "Xin chao laptop"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(sent["message"]["text"], "Xin chao laptop")

        status, received = self.request(
            "GET", f"/api/messages?token={pair['token']}&since=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(received["messages"]), 1)
        self.assertEqual(received["messages"][0]["text"], "Xin chao laptop")

        status, cleared = self.request("POST", "/api/messages/clear", {})
        self.assertEqual(status, 200)
        self.assertTrue(cleared["ok"])

        status, received = self.request("GET", "/api/messages?since=0")
        self.assertEqual(status, 200)
        self.assertEqual(received["messages"], [])

    def test_invalid_message_is_rejected(self):
        status, data = self.request("POST", "/api/messages", {"text": "   "})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_state_token_comparison(self):
        self.assertTrue(self.state.authenticate(self.state.session_token))
        self.assertFalse(self.state.authenticate("not-the-token"))
        self.assertFalse(self.state.authenticate(None))


if __name__ == "__main__":
    unittest.main()
