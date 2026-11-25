"""v1.11.2/#106: the backend config page must wire layer_cost end
to end -- an input the operator can see, populated on load, and
included in the save payload (the /api/config handler already
whitelists the key). Before the fix the page had rt_ban only, so a
backend's layer_cost was invisible and uneditable from its own UI.
Run: python3 tests/test_config_page_layer_cost.py"""
import os

def test_config_page_wires_layer_cost():
    root = os.path.join(os.path.dirname(__file__), '..')
    html = open(os.path.join(root, 'docker', 'config_page.html')).read()
    assert 'id="rt_cost"' in html                                   # the field exists
    assert "getElementById('rt_cost').value=c.routing.layer_cost" in html  # populated on load
    assert "layer_cost:document.getElementById('rt_cost').value" in html   # sent on save
    server = open(os.path.join(root, 'docker', 'server.py')).read()
    assert "'layer_cost'" in server                                # persisted by /api/config

if __name__ == '__main__':
    test_config_page_wires_layer_cost()
    print('config page layer cost OK')
