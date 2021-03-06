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

from client import Client
from common import Entry
import common

import logging
import kinetic_pb2 as messages
import operations
import threading

LOG = logging.getLogger(__name__)

class BaseAsync(Client):

    def __init__(self, *args, **kwargs):
        super(BaseAsync, self).__init__(*args, socket_timeout=None, **kwargs)
        self.unhandledException = lambda e: LOG.warn("Unhandled client exception. " + str(e))
        self.faulted = False
        self.error = None
        # private attributes
        self._pending = dict()
         # start background workers
        self._initialize()

    def _initialize(self): pass

    def _raise(self, e, onError=None):
        if onError:
            try:
                onError(e)
            except Exception as ue:
               e = ue
               onError = None

        if onError == None:
            try:
                self.unhandledException(e)
            except Exception as e:
                LOG.warn("Unhandled exception when handling unhandled exception. " + str(e))
                # just swallow it (the other option is faulting)

    def dispatch(self, fn, *args, **kwargs):
        fn(*args,**kwargs)

    def _fault_client(self, e):
        self.error = e
        self.faulted = True
        LOG.error("Connection {0} faulted. {1}".format(self,e))
        for _,onError in self._pending.itervalues():
            try:
                onError(e)
            except Exception as e2:
                LOG.error("Unhandled exception on callers code when reporting internal error. {0}".format(e2))
        self._pending = {}

    def _async_recv(self):
        if self.faulted:
            raise common.ConnectionFaulted("Connection {0} is faulted. Can't receive message when connection is on a faulted state.".format(self))

        try:
            header,value = self.network_recv()
            seq = header.command.header.ackSequence
            LOG.debug("Received message with ackSequence={0} on connection {1}.".format(seq,self))
            onSuccess,_ = self._pending[seq]
            del self._pending[seq]
            try:
                self.dispatch(onSuccess,header,value)
            except Exception as e:
                self._raise(e)
        except Exception as e:
            if not self.isConnected:
                raise common.ConnectionClosed("Connection closed by client.")
            else:
                self._fault_client(e)


    ### Override BaseClient methods

    def send(self, header, value):
        done = threading.Event()
        class Dummy : pass
        d = Dummy()
        d.error = None
        d.result = None

        def innerSuccess(header, value):
            d.result = (header, value)
            done.set()

        def innerError(e):
            d.error = e
            done.set()

        self.sendAsync(header, value, innerSuccess, innerError)

        done.wait() # TODO(Nacho): should be add a default timeout?
        if d.error: raise d.error
        return d.result

    ###

    def sendAsync(self, header, value, onSuccess, onError):
        if self.faulted: # TODO(Nacho): should we fault through onError on fault or bow up on the callers face?
            self._raise(common.ConnectionFaulted("Can't send message when connection is on a faulted state."), onError)
            return #skip the rest

        # fail fast on NotConnected
        if not self.isConnected: # TODO(Nacho): should we fault through onError on fault or bow up on the callers face?
            self._raise(common.NotConnected("Not connected."), onError)
            return #skip the rest

        def innerSuccess(header, value):
            try:
                operations._check_status(header)
                onSuccess(header, value)
            except Exception as ex:
                onError(ex)

        # get sequence
        self.update_header(header)

        # add callback to pending dictionary
        self._pending[header.command.header.sequence] = (innerSuccess, onError)

        # transmit
        self.network_send(header, value)

    def _process(self, op, *args, **kwargs):
        if not self.isConnected: raise common.NotConnected("Must call connect() before sending operations.")
        return super(BaseAsync, self)._process(op, *args, **kwargs)

    def _processAsync(self, op, onSuccess, onError, *args, **kwargs):
        if not self.isConnected: raise common.NotConnected("Must call connect() before sending operations.")

        def innerSuccess(header, value):
            onSuccess(op.parse(header, value))

        def innerError(e):
            try:
                v = op.onError(e)
                onSuccess(v)
            except Exception as e2:
                onError(e2)

        header, value = op.build(*args, **kwargs)
        self.sendAsync(header, value, innerSuccess, innerError)

    def putAsync(self, onSuccess, onError, *args, **kwargs):
        self._processAsync(operations.Put, onSuccess, onError, *args, **kwargs)

    def getAsync(self, onSuccess, onError, *args, **kwargs):
        self._processAsync(operations.Get, onSuccess, onError, *args, **kwargs)

    def getMetadataAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.GetMetadata, onSuccess, onError, *args, **kwargs)

    def deleteAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.Delete, onSuccess, onError, *args, **kwargs)

    def getNextAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.GetNext, onSuccess, onError, *args, **kwargs)

    def getPreviousAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.GetPrevious, onSuccess, onError, *args, **kwargs)

    def getKeyRangeAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.GetKeyRange, onSuccess, onError, *args, **kwargs)

    def getVersionAsync(self, onSuccess, onError, *args, **kwargs):
        return self._processAsync(operations.GetVersion, onSuccess, onError, *args, **kwargs)

    def flushAsync(self, onSuccess, onError, *args, **kwargs):
        self._processAsync(operations.Flush, onSuccess, onError, *args, **kwargs)

    def noopAsync(self, onSuccess, onError, *args, **kwargs):
        self._processAsync(operations.Noop, onSuccess, onError, *args, **kwargs)




