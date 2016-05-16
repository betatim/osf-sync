import pytest

import os
import time
import threading

from watchdog import events

from osfoffline.tasks import operations
from osfoffline.utils.log import start_logging
from osfoffline.sync.utils import EventConsolidator

from tests.sync.utils import TestSyncObserver


start_logging()


_map = {
    ('move', True): events.DirMovedEvent,
    ('move', False): events.FileMovedEvent,
    ('modify', True): events.DirModifiedEvent,
    ('modify', False): events.FileModifiedEvent,
    ('delete', True): events.DirDeletedEvent,
    ('delete', False): events.FileDeletedEvent,
    ('create', True): events.DirCreatedEvent,
    ('create', False): events.FileCreatedEvent,
}


def Event(type_, *src):
    assert len(src) < 3
    if len(src) > 1:
        assert src[0].endswith('/') == src[1].endswith('/')
    return _map[(type_, src[0].endswith('/'))](*(x.rstrip('/') for x in src))


CASES = [{
    'input': [Event('modify', '/Foo/bar/')],
    'output': []
}, {
    'input': [Event('move', '/Foo/bar', '/Foo/baz')],
    'output': [Event('move', '/Foo/bar', '/Foo/baz')]
}, {
    'input': [Event('move', '/Foo/bar/', '/Foo/baz/')],
    'output': [Event('move', '/Foo/bar/', '/Foo/baz/')]
}, {
    'input': [
        Event('move', '/Foo/bar/', '/Foo/baz/'),
        Event('move', '/Foo/bar/file.txt', '/Foo/baz/file.txt')
    ],
    'output': [Event('move', '/Foo/bar/', '/Foo/baz/')]
}, {
    'input': [
        Event('move', '/Foo/bar/file.txt', '/Foo/baz/file.txt'),
        Event('move', '/Foo/bar/', '/Foo/baz/')
    ],
    'output': [Event('move', '/Foo/bar/', '/Foo/baz/')]
}, {

######## Consolidation for same events #########################
    'input': [
        Event('move', '/parent/', '/george/'),
        Event('move', '/parent/child/', '/george/child/'),
        Event('move', '/parent/file.txt', '/george/file.txt'),
        Event('move', '/parent/child/file.txt', '/george/child/file.txt'),
        Event('move', '/parent/child/grandchild/', '/george/child/grandchild/'),
        Event('move', '/parent/child/grandchild/file.txt', '/george/child/grandchild/file.txt'),
    ],
    'output': [Event('move', '/parent/', '/george/')]
}, {
    'input': [
        Event('move', '/parent/', '/george/'),
        Event('move', '/parent/child/', '/george/child/'),
        Event('move', '/parent/child/grandchild/', '/george/child/grandchild/'),
    ],
    'output': [Event('move', '/parent/', '/george/')]
}, {
    'input': [
        Event('delete', '/parent/'),
        Event('delete', '/parent/child/'),
        Event('delete', '/parent/file.txt'),
        Event('delete', '/parent/child/file.txt'),
        Event('delete', '/parent/child/grandchild/'),
        Event('delete', '/parent/child/grandchild/file.txt')
    ],
    'output': [Event('delete', '/parent/')]
}, {
    'input': [
        Event('delete', '/parent/'),
        Event('delete', '/parent/child/'),
        Event('delete', '/parent/child/grandchild/'),
    ],
    'output': [Event('delete', '/parent/')]
}, {

######## Does not consolidate file events   #########################
    'input': [
        Event('create', '/parent/'),
        Event('create', '/parent/file.txt'),
    ],
    'output': [
        Event('create', '/parent/'),
        Event('create', '/parent/file.txt'),
    ],
}, {
    'input': [
        Event('move', '/parent/file.txt', '/george/file.txt'),
        Event('move', '/parent/child/file.txt', '/george/child/file.txt'),
        Event('move', '/parent/child/grandchild/file.txt', '/george/child/grandchild/file.txt'),
    ],
    'output': [
        Event('move', '/parent/child/grandchild/file.txt', '/george/child/grandchild/file.txt'),
        Event('move', '/parent/child/file.txt', '/george/child/file.txt'),
        Event('move', '/parent/file.txt', '/george/file.txt'),
    ]
}, {
    'input': [
        Event('delete', '/parent/file.txt'),
        Event('delete', '/parent/child/file.txt'),
        Event('delete', '/parent/child/grandchild/file.txt')
    ],
    'output': [
        Event('delete', '/parent/child/grandchild/file.txt'),
        Event('delete', '/parent/child/file.txt'),
        Event('delete', '/parent/file.txt'),
    ],
}, {

######## Consolidation for differing events #########################
    'input': [
        Event('delete', '/file.txt'),
        Event('create', '/file.txt'),
    ],
    # 'output': [Event('modify', '/file.txt')]
    'output': [Event('create', '/file.txt')]
}, {
    'input': [
        Event('delete', '/folder/'),
        Event('create', '/folder/'),
    ],
    'output': [
        # Event('delete', '/folder/'),
        Event('create', '/folder/'),
    ]
}, {
    'input': [
        Event('create', '/file.txt'),
        Event('delete', '/file.txt'),
    ],
    'output': []
}, {
    'input': [
        Event('move', '/file.txt', '/other_file.txt'),
        Event('delete', '/other_file.txt'),
    ],
    'output': [Event('delete', '/file.txt')]
}, {
    'input': [
        Event('move', '/folder1/file.txt', '/folder1/other_file.txt'),
        Event('delete', '/folder1/'),
    ],
    'output': [Event('delete', '/folder1/')]
}, {
    'input': [
        Event('create', '/file.txt'),
        Event('move', '/file.txt', '/other_file.txt'),
        Event('delete', '/other_file.txt'),
    ],
    'output': []
}, {
    'input': [
        Event('create', '/folder/'),
        Event('create', '/folder/file.txt'),
        Event('delete', '/folder/'),
    ],
    'output': []
}, {
    'input': [
        Event('modify', '/parent/file.txt'),
        Event('modify', '/parent/'),
    ],
    'output': [Event('modify', '/parent/file.txt')]
}, {
    'input': [
        Event('create', '/file.txt'),
        Event('move', '/file.txt', '/test.txt'),
    ],
    'output': [Event('create', '/test.txt')]
}, {

######## Weird cases Word/Vim/Tempfiles ############################
    'input': [
        Event('create', '/~WRL0001.tmp'),
        Event('modify', '/~WRL0001.tmp'),
        Event('move', '/file.docx', '/~WRL0005.tmp'),
        Event('move', '/~WRL0001.tmp', '/file.docx'),
        Event('delete', '/~WRL0005.tmp'),
    ],
    # 'output': [Event('modify', '/file.docx')],
    'output': [Event('create', '/file.docx')],
}, {
    'input': [
        Event('create', '/osfoffline.py'),
        Event('modify', '/osfoffline.py'),
    ],
    'output': [Event('create', '/osfoffline.py')],
}, {
    # 'input': [
    #     Event('move', '/file.docx', '/~WRL0005.tmp'),
    # ],
    # 'output': [Event('modify', '/file.docx')],
# }, {
    # 'input': [
    #     Event('move', '/~WRL0005.tmp', '/file.docx'),
    # ],
    # 'output': [Event('create', '/file.docx')],
# }, {
    'input': [
        Event('modify', '/folder/donut.txt'),
        Event('move', '/folder/donut.txt', '/test/donut.txt'),
        Event('move', '/folder/', '/test/'),
    ],
    'output': [
        Event('move', '/folder/', '/test/'),
        Event('modify', '/test/donut.txt'),
    ],
}, {
    'input': [
        Event('move', '/folder/donut.txt', '/other_folder/bagel.txt'),
        Event('move', '/folder/', '/test/'),
    ],
    'output': [
        Event('move', '/folder/donut.txt', '/other_folder/bagel.txt'),
        Event('move', '/folder/', '/test/'),
    ],
}, {
    'input': [
        Event('modify', '/donut.txt'),
        Event('move', '/donut.txt', '/bagel.txt'),
    ],
    'output': [
        Event('move', '/donut.txt', '/bagel.txt'),
        Event('modify', '/bagel.txt'),
    ],
}, {

########## Generate one offs just to be certain ####################
    'input': [Event('modify', '/folder/donut.txt')],
    'output': [Event('modify', '/folder/donut.txt')],
}, {
    'input': [Event('modify', '/folder/donut/')],
    'output': [],
}, {
    'input': [Event('delete', '/folder/donut.txt')],
    'output': [Event('delete', '/folder/donut.txt')],
}, {
    'input': [Event('delete', '/folder/donut/')],
    'output': [Event('delete', '/folder/donut/')],
}, {
    'input': [Event('create', '/folder/donut.txt')],
    'output': [Event('create', '/folder/donut.txt')],
}, {
    'input': [Event('create', '/folder/donut/')],
    'output': [Event('create', '/folder/donut/')],

# }, {
#     'input': [
#         Event('delete', '/donut.txt', sha=123)],
#         Event('create', '/bagel.txt', sha=123)],
#     ],
#     'output': [Event('move', '/bagel.txt', sha=123)]

# }, {
#     'input': [
#         Event('move', '/file1', '/file2')],
#         Event('delete', '/file1')],
#     ],
    # 'output': [Event('move', '/file1', '/file2')],
# }, {
#     'input': [
#         Event('delete', '/file2')],
#         Event('move', '/file1', '/file2')],
#     ],
    # 'output': [Event('modify', '/file2')],
# }, {

}]

CONTEXT_EVENT_MAP = {
    events.FileCreatedEvent: operations.RemoteCreateFile,
    events.FileDeletedEvent: operations.RemoteDeleteFile,
    events.FileModifiedEvent: operations.RemoteUpdateFile,
    events.FileMovedEvent: operations.RemoteMoveFile,
    events.DirCreatedEvent: operations.RemoteCreateFolder,
    events.DirDeletedEvent: operations.RemoteDeleteFolder,
    events.DirMovedEvent: operations.RemoteMoveFolder,
}


class TestEventConsolidator:

    @pytest.mark.parametrize('input, expected', [(case['input'], case['output']) for case in CASES])
    def test_event_consolidator(self, input, expected):
        consolidator = EventConsolidator()
        for event in input:
            consolidator.push(event)
        assert list(consolidator.events) == list(expected)


class TestObserver:

    def perform(self, tmpdir, event):
        if isinstance(event, events.FileModifiedEvent):
            with tmpdir.join(event.src_path).open('ab') as fobj:
                fobj.write(os.urandom(50))
        elif isinstance(event, events.FileCreatedEvent):
            with tmpdir.join(event.src_path).open('wb+') as fobj:
                fobj.write(os.urandom(50))
        elif isinstance(event, events.DirModifiedEvent):
            return
        elif isinstance(event, (events.FileMovedEvent, events.DirMovedEvent)):
            tmpdir.join(event.src_path).move(tmpdir.join(event.dest_path))
        elif isinstance(event, (events.DirDeletedEvent, events.FileDeletedEvent)):
            tmpdir.join(event.src_path).remove()
        elif isinstance(event, events.DirCreatedEvent):
            tmpdir.ensure(event.src_path, dir=True)
        else:
            raise Exception(event)

    @pytest.mark.parametrize('input, expected', [(case['input'], case['output']) for case in CASES])
    def test_event_observer(self, monkeypatch, tmpdir, input, expected):
        og_input = tuple(input)
        def local_to_db(local, node, *, is_folder=False, check_is_folder=True):
            found = False
            for event in reversed(og_input):
                if str(tmpdir.join(getattr(event, 'dest_path', ''))) == str(local):
                    return local_to_db(tmpdir.join(event.src_path), None)

                if str(tmpdir.join(event.src_path)) == str(local):
                    found = True
                    if event.event_type == events.EVENT_TYPE_CREATED:
                        return False
            return found

        # No need for database access
        monkeypatch.setattr('osfoffline.sync.local.utils.extract_node', lambda *args, **kwargs: None)
        monkeypatch.setattr('osfoffline.sync.local.utils.local_to_db', local_to_db)

        # De dup input events
        for event in tuple(input):
            for evt in tuple(input):
                if event is not evt and not isinstance(event, events.DirModifiedEvent) and event.event_type != events.EVENT_TYPE_CREATED and evt.event_type == event.event_type and getattr(evt, 'dest_path', evt.src_path).startswith(getattr(event, 'dest_path', event.src_path)):
                    input.remove(evt)

        for event in reversed(input):
            path = tmpdir.ensure(event.src_path, dir=event.is_directory)

            if isinstance(event, (events.FileMovedEvent, events.DirMovedEvent)):
                tmpdir.ensure(event.dest_path, dir=event.is_directory).remove()

            if isinstance(event, (events.FileCreatedEvent, events.DirCreatedEvent)):
                path.remove()

        observer = TestSyncObserver(tmpdir.strpath)
        threading.Thread(target=observer.start).start()

        time.sleep(1)

        for event in input:
            self.perform(tmpdir, event)

        time.sleep(1)

        observer.stop()
        observer.flush()

        # Clear cached instance of Observer
        del type(TestSyncObserver)._instances[TestSyncObserver]

        assert len(expected) == len(observer._events)

        for event, context in zip(expected, observer._events):
            assert CONTEXT_EVENT_MAP[type(event)] == type(context)
            assert str(tmpdir.join(event.src_path)) == str(context.local)
