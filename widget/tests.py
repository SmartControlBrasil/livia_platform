from django.test import TestCase


class WidgetTests(TestCase):
    def test_widget_js_contains_fetch(self):
        response = self.client.get("/widget.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript; charset=utf-8")
        self.assertIn("fetch", response.content.decode("utf-8"))

    def test_demo_page_loads_widget_script(self):
        response = self.client.get("/demo/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('/widget.js" data-tenant="smart-control-brasil"', response.content.decode("utf-8"))
