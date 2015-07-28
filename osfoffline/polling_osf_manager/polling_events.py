from osfoffline.utils.path import ProperPath
from osfoffline.polling_osf_manager.osf_query import OSFQuery
import os
import shutil
import asyncio

class PollingEvent(object):
    def __init__(self, path):
        assert isinstance(path, str)

    @asyncio.coroutine
    def run(self):
        pass

class CreateFolder(PollingEvent):
    def __init__(self, path):
        super().__init__(path)
        self.path = ProperPath(path, is_dir=True)
        assert self.path

    @asyncio.coroutine
    def run(self):
        # create local node folder on filesystem
        if not os.path.exists(self.path.full_path):
            os.makedirs(self.path.full_path)


class CreateFile(PollingEvent):
    def __init__(self, path, download_url, osf_query):
        super().__init__(path)
        self.path = ProperPath(path, is_dir=False)
        self.osf_query = osf_query
        self.download_url = download_url
        assert self.path
        assert isinstance(self.osf_query, OSFQuery)
        assert isinstance(self.download_url, str)

    @asyncio.coroutine
    def run(self):
        yield from _download_file(self.path, self.download_url, self.osf_query)


class RenameFolder(PollingEvent):
    def __init__(self, old_path, new_path):
        super().__init__(old_path)
        assert isinstance(new_path, str)

        self.old_path = self.path
        self.new_path = ProperPath(new_path, is_dir=True)
        assert self.old_path
        assert self.new_path

    @asyncio.coroutine
    def run(self):
        _rename(self.old_path, self.new_path)

class RenameFile(PollingEvent):
    def __init__(self, old_path, new_path):
        super().__init__(old_path)
        assert isinstance(new_path, str)

        self.old_path = self.path
        self.new_path = ProperPath(new_path, is_dir=False)
        assert self.old_path
        assert self.new_path

    @asyncio.coroutine
    def run(self):
        yield from _rename(self.old_path, self.new_path)

class UpdateFile(PollingEvent):
    def __init__(self, path, download_url, osf_query):
        super().__init__(path)
        self.path = ProperPath(path, is_dir=False)
        self.osf_query = osf_query
        self.download_url = download_url
        assert isinstance(self.osf_query, OSFQuery)
        assert isinstance(self.download_url, str)

    @asyncio.coroutine
    def run(self):
        yield from _download_file(self.path, self.download_url, self.osf_query)


class DeleteFolder(PollingEvent):
    def __init__(self, path):
        super().__init__(path)
        self.path=ProperPath(path, is_dir=True)

    @asyncio.coroutine
    def run(self):
        # this works on systems that use file descriptors.
        # thus, linux, mac are supported.
        # todo: is windows supported??
        if shutil.rmtree.avoids_symlink_attacks:
            shutil.rmtree(
                self.path.full_path,
                onerror=lambda a, b, c: print('local node not deleted because not exists.')
            )
        else:
            raise NotImplementedError


class DeleteFile(PollingEvent):
    def __init__(self, path):
        super().__init__(path)
        self.path=ProperPath(path, is_dir=False)

    @asyncio.coroutine
    def run(self):
        try:
            os.remove(self.path.full_path)
        except FileNotFoundError:
            print('file not deleted because does not exist on local filesystem. inside delete_local_file_folder (2)')

@asyncio.coroutine
def _download_file(path, url, osf_query):
    assert isinstance(path, ProperPath)
    assert isinstance(url, str)

    resp = yield from osf_query.make_request(url)
    with open(path.full_path, 'wb') as fd:
        while True:
            chunk = yield from resp.content.read(2048)
            if not chunk:
                break
            fd.write(chunk)
    resp.close()

@asyncio.coroutine
def _rename(old_path, new_path):
    assert isinstance(old_path, ProperPath)
    assert isinstance(new_path, ProperPath)
    try:
        os.renames(old_path.full_path, new_path.full_path)
    except FileNotFoundError:
        print('renaming of file/folder failed because file/folder not there')
