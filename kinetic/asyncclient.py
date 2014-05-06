# Copyright (C) 2014 Seagate Technology.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

#@author: Ignacio Corderi

import logging
import eventlet
from eventlet.queue import Queue

from eventlet.green import socket
import baseasync
import common

LOG = logging.getLogger(__name__)

DEFAULT_POOL_SIZE = 100
DEFAULT_MAX_QUEUE_SIZE = 20
MAX_PENDING = 10

class AsyncClient(baseasync.BaseAsync):

    def __init__(self, *args, **kwargs):
        super(AsyncClient, self).__init__(*args, **kwargs)
        self.pool = eventlet.greenpool.GreenPool(DEFAULT_POOL_SIZE)
        self.reader_thread = None
        self.writer_thread = None
        self.queue = Queue(DEFAULT_MAX_QUEUE_SIZE)
        self.max_pending = MAX_PENDING
        self.closing = False

    def build_socket(self):
        return socket.socket()

    def connect(self):
        super(AsyncClient, self).connect()
        self.closing = False
        self.reader_thread = eventlet.greenthread.spawn(self._reader_run)
        self.writer_thread = eventlet.greenthread.spawn(self._writer_run)

    def dispatch(self, fn, *args, **kwargs):
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("Dispatching: Pending {0}".format(len(self._pending)))
        self.pool.spawn_n(fn,*args, **kwargs)

    def shutdown(self):
        self.closing = True
        if len(self._pending) + self.queue.qsize() == 0:
            self._end_close()

    def close(self):
        self.shutdown()
        self.flush()

    def _end_close(self):
        self.writer_thread.kill()
        self.reader_thread.kill()

        self.dispatch(super(AsyncClient, self).close)

        self.writer_thread = None
        self.reader_thread = None

    def sendAsync(self, header, value, onSuccess, onError):
        if self.closing:
            raise common.ConnectionClosed("Client is closing, can't queue more operations.")
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("Queue: {0}".format(self.queue.qsize()))
        self.queue.put((header, value, onSuccess, onError))
        eventlet.sleep(0)

    def flush(self):
        self.queue.join()

    def send(self, header, value):
        done = eventlet.event.Event()
        class Dummy : pass
        d = Dummy()
        d.error = None
        d.result = None

        def innerSuccess(header, value):
            d.result = (header, value)
            done.send()

        def innerError(e):
            d.error = e
            done.send()

        self.sendAsync(header, value, innerSuccess, innerError)

        done.wait() # TODO(Nacho): should be add a default timeout?
        if d.error: raise d.error
        return d.result

    def _writer_run(self):
        while self.isConnected and not self.faulted:
            try:
                while len(self._pending) > self.max_pending:
                    eventlet.sleep(0)
                (header, value, onSuccess, onError) = self.queue.get()
                super(AsyncClient, self).sendAsync(header, value, onSuccess, onError)
            except common.ConnectionFaulted: pass
            except common.ConnectionClosed: pass
            except Exception as ex:
                self._fault_client(ex)

            # Yield execution, don't starve the reader
            eventlet.sleep(0)

    def _reader_run(self):
        while self.isConnected and not self.faulted:
            try:
                self._async_recv()
                self.queue.task_done()
                if self.closing  and len(self._pending) + self.queue.qsize() == 0:
                    self._end_close()
            except common.ConnectionFaulted: pass
            except Exception as ex:
                self._fault_client(ex)

            # Yield execution, don't starve the writer
            # eventlet.sleep(0)

            #eventlet.sleep()
