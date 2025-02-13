# Copyright 2020 Amazon.com, Inc. or its affiliates.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
#    http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file.
# This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions
# and limitations under the License.

import os
import uuid

from neptune_python_utils.endpoints import Endpoints
from gremlin_python.driver.client import Client
from gremlin_python.driver.serializer import Processor, GraphSONMessageSerializer
from gremlin_python.structure.io import graphsonV3d0
from gremlin_python.driver import request
from gremlin_python.process import traversal
from gremlin_python import statics
from gremlin_python.structure.graph import Graph
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.anonymous_traversal import *
from gremlin_python.process.strategies import *
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.traversal import *
from aiohttp.client_exceptions import ClientError
        
        
class GremlinUtils:
    
    @classmethod
    def init_statics(cls, globals):
        
        statics.load_statics(globals)

        for s in ['range', 'map', 'min', 'sum', 'property', 'max']:
            if s in globals:
                del globals[s]
    
    def __init__(self, endpoints=None):
                
        if endpoints is None:
            self.endpoints = Endpoints()
        else:
            self.endpoints = endpoints
            
        self.connections = []
        
    def close(self):
        for connection in self.connections:
            connection.close()
            
    def remote_connection(self, 
                          show_endpoint=False,
                          protocol_factory=None,
                          pool_size=None,
                          max_workers=None,
                          message_serializer=None,
                          graphson_reader=None,
                          graphson_writer=None,
                          **kwargs):
        
        gremlin_endpoint = self.endpoints.gremlin_endpoint()
        
        if show_endpoint:
            print('gremlin: {}'.format(gremlin_endpoint))
        
        retry_count = 0
         
        while True:
            try:
                request_parameters = gremlin_endpoint.prepare_request()

                connection = DriverRemoteConnection(
                    request_parameters.uri, 
                    'g',
                    protocol_factory=protocol_factory,
                    pool_size=pool_size,
                    max_workers=max_workers,
                    message_serializer=message_serializer,
                    graphson_reader=graphson_reader,
                    graphson_writer=graphson_writer,
                    headers=request_parameters.headers,
                    **kwargs)
                    
                self.connections.append(connection)
                
                return connection
            except ClientError as e:
                exc_info = sys.exc_info()
                if retry_count < 3:
                    retry_count+=1
                    print('Connection timeout. Retrying...')
                else:
                    raise exc_info[0].with_traceback(exc_info[1], exc_info[2])
                    
    def traversal_source(self, show_endpoint=True, connection=None):
        if connection is None:
            connection = self.remote_connection(show_endpoint)
        return traversal().withRemote(connection)
    
    def client(self, pool_size=None, max_workers=None, **kwargs):
        
        gremlin_endpoint = self.endpoints.gremlin_endpoint()
        request_parameters = gremlin_endpoint.prepare_request()
            
        return Client(
            request_parameters.uri,
            'g',
            pool_size=pool_size,
            max_workers=max_workers,
            headers=request_parameters.headers,
            **kwargs)
        
    def sessioned_client(self, session_id=None, pool_size=1, max_workers=None, **kwargs):
        
        gremlin_endpoint = self.endpoints.gremlin_endpoint()
        request_parameters = gremlin_endpoint.prepare_request()

        return SessionedClient(
            request_parameters.uri, 
            'g', 
            uuid.uuid4().hex if session_id is None else session_id,
            pool_size=pool_size, 
            max_workers=max_workers,
            headers=request_parameters.headers,
            **kwargs)
            
        
class Session(Processor):

    def authentication(self, args):
        return args

    def eval(self, args):
        return args
    
    def close(self, args):
        return args
    
class ExtendedGraphSONSerializersV3d0(GraphSONMessageSerializer):
     
    def __init__(self):
        reader = graphsonV3d0.GraphSONReader()
        writer = graphsonV3d0.GraphSONWriter()
        version = b"application/vnd.gremlin-v3.0+json"
        super(ExtendedGraphSONSerializersV3d0, self).__init__(reader, writer, version)
        self.session = Session(writer)

class SessionedClient(Client):
    
    def __init__(self, url, traversal_source, session_id, protocol_factory=None,
                 transport_factory=None, pool_size=None, max_workers=None,
                 message_serializer=ExtendedGraphSONSerializersV3d0(), username="", password="",
                 headers=None, **kwargs):
        super(SessionedClient, self).__init__(url, traversal_source, protocol_factory,
                 transport_factory, pool_size, max_workers,
                 message_serializer, username, password, headers=headers, **kwargs)
        self._session_id = session_id
        
    def __enter__(self):
        return self
        
    def __exit__(self, type, value, traceback):
        self.close()
        
    def submitAsync(self, message, bindings=None, request_options=None):
        if isinstance(message, str):
            message = request.RequestMessage(
                processor='session', 
                op='eval',
                args={'gremlin': message,
                      'aliases': {'g': self._traversal_source},
                      'session': self._session_id,
                      'manageTransaction': False})
            if bindings:
                message.args.update({'bindings': bindings})
        else:
            raise Exception('Unsupported message type: {}'.format(type(message)))
        conn = self._pool.get(True)
        if request_options:
            message.args.update(request_options)
        return conn.write(message)
    
    def close(self):
        message = request.RequestMessage(
                processor='session', 
                op='close',
                args={'session': self._session_id,
                      'manageTransaction': False,
                      'force': False})
        conn = self._pool.get(True)
        conn.write(message).result()
        super(SessionedClient, self).close()
        
        