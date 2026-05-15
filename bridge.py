"""
Bridge Server - connects n8n HTTP Request nodes to tekscrum_pipeline.py
Runs on http://localhost:5680
Keep this terminal open while n8n is running.
"""

import json
import subprocess
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PIPELINE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tekscrum_pipeline.py")
PORT = 5680


class BridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/process":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)

            try:
                lead_data = json.loads(post_data.decode("utf-8"))
                biz_name = lead_data.get("business_name", lead_data.get("Company Name", "Unknown"))
                print(f"\n{'='*60}")
                print(f"Processing lead: {biz_name}")
                print(f"Email: {lead_data.get('email', 'N/A')}")
                print(f"Website: {lead_data.get('website', 'N/A')}")
                print(f"{'='*60}")

                # Call the pipeline script
                result = subprocess.run(
                    [sys.executable, PIPELINE_SCRIPT, json.dumps(lead_data)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                )

                if result.returncode != 0:
                    print(f"Script STDERR: {result.stderr[:500]}")
                    
                # DEBUG: Save exact output to file
                with open("bridge_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"\n--- LEAD: {biz_name} ---\n")
                    f.write(f"Return Code: {result.returncode}\n")
                    f.write(f"STDOUT:\n{result.stdout}\n")
                    f.write(f"STDERR:\n{result.stderr}\n")

                # Find the JSON output (last line of stdout)
                output_lines = result.stdout.strip().split("\n")
                final_json = "{}"
                for line in reversed(output_lines):
                    line = line.strip()
                    if line.startswith("{"):
                        final_json = line
                        break

                # Validate it's valid JSON
                parsed = json.loads(final_json)
                print(f"Result: status={parsed.get('status', 'OK')}, email_body={'YES' if parsed.get('email_body') else 'NO'}")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(final_json.encode("utf-8"))
                
                print("Waiting 20 seconds before next lead to prevent Gemini API rate limit...")
                import time
                time.sleep(20)

            except Exception as e:
                print(f"ERROR: {str(e)}")
                error_response = json.dumps({
                    "status": "ERROR",
                    "error": str(e),
                    "email": lead_data.get("email", "") if "lead_data" in dir() else "",
                    "business_name": "",
                })
                self.send_response(200)  # Return 200 so n8n doesn't crash
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(error_response.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP logs to keep output clean."""
        pass


def run(server_class=HTTPServer, handler_class=BridgeHandler, port=PORT):
    server_address = ("", port)
    httpd = server_class(server_address, handler_class)
    print(f"{'='*60}")
    print(f"  Bridge Server started on http://localhost:{port}")
    print(f"  Waiting for n8n to send leads...")
    print(f"  Keep this terminal OPEN while running n8n.")
    print(f"{'='*60}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
