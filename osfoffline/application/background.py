# -*- coding: utf-8 -*-
import asyncio
import logging
import threading

from asyncio import CancelledError, InvalidStateError

from osfoffline import settings
from osfoffline.database import models
from osfoffline.database import session
from osfoffline.sync.local import LocalSync
from osfoffline.sync.remote import RemoteSync
from osfoffline.tasks import Intervention, Notification
from osfoffline.tasks.queue import OperationsQueue


logger = logging.getLogger(__name__)


class BackgroundWorker(threading.Thread):

    def __init__(self):
        super().__init__()

        # Start out with null variables for NoneType errors rather than Attribute Errors
        self.user = None

        self.loop = None
        self.poller = None
        self.observer = None

        # TODO: Find a good fix for ulimit setting
        # try:
        #     self.observer.start()  # start
        # except OSError as e:
        #     # FIXME: Document these limits and provide better user notification.
        #     #    See http://pythonhosted.org/watchdog/installation.html for limits.
        #     raise RuntimeError('Limit of watched items reached') from e

    def set_intervention_cb(self, cb):
        Intervention().set_callback(cb)

    def set_notification_cb(self, cb):
        Notification().set_callback(cb)

    def _ensure_event_loop(self):
        """Ensure the existance of an eventloop
        Useful for contexts where get_event_loop() may raise an exception.
        Such as multithreaded applications

        :returns: The new event loop
        :rtype: BaseEventLoop
        """
        try:
            return asyncio.get_event_loop()
        except (AssertionError, RuntimeError):
            asyncio.set_event_loop(asyncio.new_event_loop())

        # Note: No clever tricks are used here to dry up code
        # This avoids an infinite loop if settings the event loop ever fails
        return asyncio.get_event_loop()

    def run(self):
        logger.debug('Background worker starting')
        self.loop = self._ensure_event_loop()
        self.loop.set_debug(settings.DEBUG)

        self.user = session.query(models.User).one()

        self.operation_queue = OperationsQueue()
        self.operation_queue_task = asyncio.ensure_future(self.operation_queue.start())
        self.operation_queue_task.add_done_callback(self._handle_exception)

        logger.debug('Initializing Remote Sync')
        self.remote_sync = RemoteSync(self.operation_queue, self.user)
        self.loop.run_until_complete(self.remote_sync.initialize())

        logger.debug('Starting Local Sync')
        self.local_sync = LocalSync(self.user, self.operation_queue)
        self.local_sync.start()

        logger.debug('Starting Remote Sync')
        self.remote_sync_task = asyncio.ensure_future(self.remote_sync.start())
        self.remote_sync_task.add_done_callback(self._handle_exception)

        logging.debug('Starting background event loop')
        try:
            self.loop.run_forever()
        except Exception as e:
            logger.exception(e)
        logging.debug('Background event loop exited')

    def _handle_exception(self, future):
        """
        Handle exception raised while executing a coroutine
        In present architecture this will not catch failures of individual tasks; an uncaught exception in a failed
            task will halt execution of entire queue from that point onward
        """
        try:
            raise future.exception()
        except (CancelledError, InvalidStateError):
            pass
        except Exception:
            logger.exception('Unhandled exception from background worker task {}:'.format(future))

        if self.remote_sync_task == future:
            logger.info('Restarting remote sync task')
            self.remote_sync_task = asyncio.ensure_future(self.remote_sync.start())
            self.remote_sync_task.add_done_callback(self._handle_exception)
        else:
            logger.info('Restarting operations queue')
            self.operation_queue_task = asyncio.ensure_future(self.operation_queue.start())
            self.operation_queue_task.add_done_callback(self._handle_exception)

    def sync_now(self):
        self.loop.call_soon_threadsafe(asyncio.ensure_future, self.remote_sync.sync_now())

    def stop(self):
        # Note: This method is **NOT called from this current thread**
        # All method/function/routines/etc MUST be thread safe from here out
        logger.debug('Stopping background worker')

        logger.debug('Stopping operation queue task')
        self.loop.call_soon_threadsafe(self.operation_queue_task.cancel)

        logger.debug('Stopping remote sync task')
        self.loop.call_soon_threadsafe(self.remote_sync_task.cancel)

        logger.debug('Stopping local sync task')
        self.local_sync.stop()

        logger.debug('Stopping the event loop')
        # Note: this is what actually stops the current thread
        # Calls self.join, be careful with what is on the event loop
        self._stop_loop(close=True)

    def _stop_loop(self, close=False):
        """ WARNING: Only pass in 'close' if you plan on creating a new loop afterwards
        """
        if not self.loop:
            logger.warning('loop never initialized')
            return

        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            logging.debug('Stopped event loop')
            # TODO: This should be done in a way as to not cause an error
            try:
                self.join()
            except RuntimeError:
                pass

        if close:
            # TODO: This should be done in a way as to not cause an error
            try:
                self.loop.close()
                logging.debug('Closed event loop')
            except RuntimeError:
                pass
