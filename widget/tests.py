from django.test import TestCase


class WidgetTests(TestCase):
    def test_widget_js_contains_fetch(self):
        response = self.client.get("/widget.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript; charset=utf-8")
        content = response.content.decode("utf-8")
        self.assertIn("fetch", content)
        self.assertIn('getAttribute("data-api-url")', content)
        self.assertIn('new URL("/api/chat/", scriptEl.src).href', content)
        self.assertIn("session_key: sessionId", content)
        self.assertIn("left: 20px", content)
        self.assertNotIn("right: 20px", content)

    def test_demo_page_loads_widget_script(self):
        response = self.client.get("/demo/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('/widget.js" data-tenant="smart-control-brasil"', response.content.decode("utf-8"))
