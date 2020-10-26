#!/usr/bin/env python3
# Copyright 2020 Harry Pidcock
# See LICENSE file for licensing details.

import logging
import os
import subprocess
import json

import ops.lib

from ops.charm import (
    CharmBase,
    CharmEvents,
)
from ops.main import main
from ops.framework import (
    EventBase,
    EventSource,
    StoredState,
)
from ops.model import (
    ActiveStatus,
    WaitingStatus,
)


logger = logging.getLogger(__name__)

pgsql = ops.lib.use("pgsql", 1, "postgresql-charmers@lists.launchpad.net")

DATABASE_NAME = 'pgweb'


class PGWebDBMasterAvailableEvent(EventBase):
    pass


class PGWebCharmEvents(CharmEvents):
    """Custom charm events."""

    db_master_available = EventSource(PGWebDBMasterAvailableEvent)


class PGWebCharm(CharmBase):
    state = StoredState()
    on = PGWebCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.start, self._start)
        self.framework.observe(self.on.upgrade_charm, self._start)
        self.state.set_default(db_conn_str=None, db_uri=None, db_ro_uris=[])
        self.db = pgsql.PostgreSQLClient(self, 'db')
        self.framework.observe(self.db.on.database_relation_joined, self._on_database_relation_joined)
        self.framework.observe(self.db.on.master_changed, self._on_master_changed)
        self.framework.observe(self.db.on.standby_changed, self._on_standby_changed)
        self.framework.observe(self.on.db_master_available, self._start)

    def _on_database_relation_joined(self, event: pgsql.DatabaseRelationJoinedEvent):
        """Handle db-relation-joined."""
        if self.model.unit.is_leader():
            # Provide requirements to the PostgreSQL server.
            event.database = DATABASE_NAME  # Request database named mydbname
            # event.extensions = ['citext']  # Request the citext extension installed
        elif event.database != DATABASE_NAME:
            # Leader has not yet set requirements. Defer, incase this unit
            # becomes leader and needs to perform that operation.
            event.defer()
            return

    def _on_master_changed(self, event: pgsql.MasterChangedEvent):
        """Handle changes in the primary database unit."""
        if event.database != DATABASE_NAME:
            # Leader has not yet set requirements. Wait until next
            # event, or risk connecting to an incorrect database.
            return

        self.state.db_conn_str = None if event.master is None else event.master.conn_str
        self.state.db_uri = None if event.master is None else event.master.uri

        if event.master is None:
            return

        self.on.db_master_available.emit()

    def _on_standby_changed(self, event: pgsql.StandbyChangedEvent):
        """Handle changes in the secondary database unit(s)."""
        if event.database != DATABASE_NAME:
            # Leader has not yet set requirements. Wait until next
            # event, or risk connecting to an incorrect database.
            return

        self.state.db_ro_uris = [c.uri for c in event.standbys]


    def _start(self, event):
        if not self.state.db_uri:
            self.unit.status = WaitingStatus('Waiting for database relation')
            event.defer()
            return

        if not self.unit.is_leader():
            self.unit.status = ActiveStatus()
            return

        if self._is_running():
            self.unit.status = ActiveStatus()
            return

        cmd = "pebble start --socket /charm/containers/pgweb/pebble.sock --env 'DATABASE_URL={}' -- /usr/bin/pgweb --bind=0.0.0.0 --listen=8081".format(self.state.db_uri)
        logger.error(cmd)
        os.system(cmd)
        self.unit.status = ActiveStatus("pgweb started")

    def _stop(self, event):
        os.system("pebble signal --socket /charm/containers/pgweb/pebble.sock")

    def _is_running(self):
        res = subprocess.run(["pebble", "status", "--socket", "/charm/containers/pgweb/pebble.sock"], capture_output=True, text=True)
        logger.error(res.returncode)
        logger.error(res.stdout)
        s = json.loads(res.stdout)
        return s["status"] == "running"




if __name__ == "__main__":
    main(PGWebCharm, use_juju_for_storage=False)
