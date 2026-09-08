from PySide6.QtWidgets import QApplication
from casu.epg import StreamChannel, StreamCatalog
from mpcasu_qt.main_window import EpgPage

def test_iptv_filters_and_pages_preserve_channel_identity():
    app = QApplication.instance() or QApplication([])
    page = EpgPage()
    page._catalog = StreamCatalog(tuple(StreamChannel(f'https://example.org/{i}', f'Channel {i}', group='News' if i % 2 else 'Sports') for i in range(250)))
    page._render()
    assert page._grid.count() == 100
    page._turn_channels(1)
    assert page._grid.count() == 100
    page._turn_channels(1)
    assert page._grid.count() == 50
    page._channel_group.setCurrentIndex(page._channel_group.findData('News'))
    assert page._channel_page == 0
    assert page._grid.count() == 100
    page._channel_search.setText('Channel 249')
    assert page._grid.count() == 1
    page._channel_search.setText('no such channel')
    assert page._grid.count() == 0
    assert not page._next_channels.isEnabled()
    page.close()
