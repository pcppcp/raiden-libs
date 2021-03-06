import logging
from typing import List, Callable
from urllib.parse import quote

import gevent
from matrix_client.client import CACHE, MatrixClient
from matrix_client.errors import MatrixRequestError
from matrix_client.user import User

from .room import Room
from .utils import geventify_callback


logger = logging.getLogger(__name__)


class GMatrixClient(MatrixClient):
    """ Gevent-compliant MatrixClient subclass """

    def __init__(
            self,
            base_url: str,
            token: str = None,
            user_id: str = None,
            valid_cert_check: bool = True,
            sync_filter_limit: int = 20,
            cache_level: CACHE = CACHE.ALL
    ) -> None:
        super().__init__(
            base_url,
            token,
            user_id,
            valid_cert_check,
            sync_filter_limit,
            cache_level
        )
        self.should_listen = False
        self.sync_thread = None

    def listen_forever(
        self,
        timeout_ms: int = 30000,
        exception_handler: Callable = None,
        bad_sync_timeout: int = 5
    ):
        """
        Keep listening for events forever.
        Args:
            timeout_ms: How long to poll the Home Server for before retrying.
            exception_handler: Optional exception handler function which can
                be used to handle exceptions in the caller thread.
            bad_sync_timeout: Base time to wait after an error before retrying.
                Will be increased according to exponential backoff.
        """
        _bad_sync_timeout = bad_sync_timeout
        self.should_listen = True
        while self.should_listen:
            try:
                self._sync(timeout_ms)
                _bad_sync_timeout = bad_sync_timeout
            except MatrixRequestError as e:
                logger.warning('A MatrixRequestError occured during sync.')
                if e.code >= 500:
                    logger.warning(
                        'Problem occured serverside. Waiting %i seconds',
                        _bad_sync_timeout
                    )
                    gevent.sleep(_bad_sync_timeout)
                    _bad_sync_timeout = min(_bad_sync_timeout * 2, self.bad_sync_timeout_limit)
                else:
                    raise
            except Exception as e:
                logger.exception('Exception thrown during sync')
                if exception_handler is not None:
                    exception_handler(e)
                else:
                    raise

    def start_listener_thread(self, timeout_ms: int = 30000, exception_handler: Callable = None):
        """
        Start a listener greenlet to listen for events in the background.
        Args:
            timeout_ms: How long to poll the Home Server for before retrying.
            exception_handler: Optional exception handler function which can
                be used to handle exceptions in the caller thread.
        """
        self.should_listen = True
        self.sync_thread = gevent.spawn(self.listen_forever, timeout_ms, exception_handler)

    def search_user_directory(self, term: str) -> List[User]:
        """
        Search user directory for a given term, returning a list of users
        Args:
            term: term to be searched for
        Returns:
            user_list: list of users returned by server-side search
        """
        response = self.api._send(
            'POST',
            '/user_directory/search',
            {
                'search_term': term
            }
        )
        try:
            return [
                User(self.api, _user['user_id'], _user['display_name'])
                for _user in response['results']
            ]
        except KeyError:
            return []

    def modify_presence_list(
        self,
        add_user_ids: List[str] = None,
        remove_user_ids: List[str] = None
    ):
        if add_user_ids is None:
            add_user_ids = []
        if remove_user_ids is None:
            remove_user_ids = []
        return self.api._send(
            'POST',
            f'/presence/list/{quote(self.user_id)}',
            {
                'invite': add_user_ids,
                'drop': remove_user_ids
            }
        )

    def get_presence_list(self) -> List[dict]:
        return self.api._send(
            'GET',
            f'/presence/list/{quote(self.user_id)}',
        )

    def set_presence_state(self, state: str):
        return self.api._send(
            'PUT',
            f'/presence/{quote(self.user_id)}/status',
            {
                'presence': state
            }
        )

    def typing(self, room: Room, timeout: int=5000):
        """
        Send typing event directly to api

        Args:
            room: room to send typing event to
            timeout: timeout for the event, in ms
        """
        path = f'/rooms/{quote(room.room_id)}/typing/{quote(self.user_id)}'
        return self.api._send('PUT', path, {'typing': True, 'timeout': timeout})

    def add_invite_listener(self, callback: Callable):
        super().add_invite_listener(geventify_callback(callback))

    def add_leave_listener(self, callback: Callable):
        super().add_leave_listener(geventify_callback(callback))

    def add_presence_listener(self, callback: Callable):
        return super().add_presence_listener(geventify_callback(callback))

    def add_listener(self, callback: Callable, event_type: str = None):
        return super().add_listener(geventify_callback(callback), event_type)

    def add_ephemeral_listener(self, callback: Callable, event_type: str = None):
        return super().add_ephemeral_listener(geventify_callback(callback), event_type)

    def _mkroom(self, room_id: str):
        """ Uses a geventified Room subclass """
        self.rooms[room_id] = Room(self, room_id)
        return self.rooms[room_id]
