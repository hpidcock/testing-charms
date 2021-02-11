#!/usr/bin/env python3
# Copyright 2020 Harry Pidcock
# See LICENSE file for licensing details.

import logging
import os

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
        self.framework.observe(self.on.pgweb_workload_ready, self._pgweb_ready)
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
        logger.error("start hook")
        self.unit.status = WaitingStatus("cockroachdb pending")

    def _stop(self, event):
        logger.error("stop hook")
        self.unit.get_container("pgweb").stop("pgweb")
        self.unit.status = WaitingStatus("cockroachdb stopping")

    def _pgweb_ready(self, event):
        if not self.state.db_uri:
            self.unit.status = WaitingStatus('Waiting for database relation')
            event.defer()
            return

        logger.error("pgweb workload ready hook")
        if not os.path.exists("/charm/containers/pgweb/pebble/layers"):
            os.makedirs("/charm/containers/pgweb/pebble/layers")
        with open("/charm/containers/pgweb/pebble/layers/0.yaml", "w") as f:
            f.write("""
summary: pgweb layer
description: pgweb layer
services:
    pgweb:
        override: replace
        summary: pgweb service
        command: /usr/bin/pgweb --bind=0.0.0.0 --listen=8081
        environment:
            - DATABASE_URL: {}
""".format(self.state.db_uri))

        self.unit.get_container("pgweb").start("pgweb")
        self.unit.status = ActiveStatus("pgweb started")



if __name__ == "__main__":
    main(PGWebCharm, use_juju_for_storage=False)
