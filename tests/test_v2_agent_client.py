"""Agent client turns every transport/protocol failure into a catchable error."""
import socket
import threading
import unittest

from agent_client import ProposalError, propose

KEY = "k" * 40


def fake_server(response: bytes):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)

    def serve():
        conn, _ = sock.accept()
        conn.recv(65536)
        conn.sendall(response)
        conn.close()
        sock.close()

    threading.Thread(target=serve, daemon=True).start()
    return sock.getsockname()[1]


class AgentClientV2Tests(unittest.TestCase):
    def test_bad_status_line_is_wrapped(self):
        port = fake_server(b"NOT HTTP AT ALL\r\n\r\n")
        with self.assertRaises(ProposalError):  # milestone 3 leaked http.client.BadStatusLine
            propose("read_file", "demo-note", "r", port=port, key=KEY)

    def test_oversized_or_malformed_receipt(self):
        for body in (b"x" * 600, b"not json", b'{"id": "../../etc", "status": "pending"}'):
            with self.subTest(body=body[:20]):
                port = fake_server(b"HTTP/1.0 202 Accepted\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
                with self.assertRaises(ProposalError):
                    propose("read_file", "demo-note", "r", port=port, key=KEY)

    def test_rejected_status_and_input_validation(self):
        port = fake_server(b"HTTP/1.0 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
        with self.assertRaises(ProposalError):
            propose("read_file", "demo-note", "r", port=port, key=KEY)
        for kwargs in ({"key": None}, {"key": "short"}, {"port": 0}, {"port": "8765"}):
            params = {"port": 1, "key": KEY}
            params.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                propose("read_file", "demo-note", "r", **params)

    def test_proposal_error_is_runtime_error_for_tool_adapter(self):
        self.assertTrue(issubclass(ProposalError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
