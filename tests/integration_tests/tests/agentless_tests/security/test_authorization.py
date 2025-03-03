from cloudify_rest_client.exceptions import ForbiddenError, NotModifiedError
from integration_tests.tests.test_cases import AgentlessTestCase
from integration_tests.tests.constants import USER_ROLE, ADMIN_ROLE


TENANT_USER = 'user'
TENANT_VIEWER = 'viewer'
TENANT_MANAGER = 'manager'

USERS = {
    'u1': ADMIN_ROLE,
    'u2': USER_ROLE,
    'u3': USER_ROLE,
    'u4': USER_ROLE,
    'u5': USER_ROLE,
}

GROUPS = {'g1': ['u2', 'u4'], 'g2': [], 'g3': ['u1', 'u3'], 'g4': ['u3']}

TENANTS = {
    't1': {
        'users': {
            'u2': TENANT_MANAGER, 'u3': TENANT_USER, 'u4': TENANT_VIEWER
        },
        'groups': {}
    },
    't2': {
        'users': {},
        'groups': {'g3': TENANT_VIEWER, 'g4': TENANT_MANAGER}
    },
    't3': {
        'users': {'u5': TENANT_MANAGER, 'u1': TENANT_VIEWER},
        'groups': {'g1': TENANT_USER, 'g2': TENANT_MANAGER}
    },
    't4': {
        'users': {},
        'groups': {}
    },
    't5': {
        'users': {'u5': TENANT_MANAGER, 'u2': TENANT_USER, 'u3': TENANT_USER},
        'groups': {'g2': TENANT_USER}
    }
}


def _collect_roles(user, tenant):
    roles = [USERS[user]]
    if user in TENANTS[tenant]['users']:
        roles.append(TENANTS[tenant]['users'][user])
    for group in TENANTS[tenant]['groups']:
        if user in GROUPS[group]:
            roles.append(TENANTS[tenant]['groups'][group])
    return roles


def _is_authorized(user, tenant, allowed_roles):
    user_roles = _collect_roles(user, tenant)
    return any(role in allowed_roles for role in user_roles)


class AuthorizationTest(AgentlessTestCase):
    def setUp(self):
        super(AuthorizationTest, self).setUp()
        # create users
        for user in USERS:
            self.client.users.create(user, '12345', USERS[user])

        # create groups and add users to groups
        for group in GROUPS:
            self.client.user_groups.create(group, USER_ROLE)
            for user in GROUPS[group]:
                self.client.user_groups.add_user(user, group)

        # create tenants and add users and groups to tenants
        for tenant in TENANTS:
            self.client.tenants.create(tenant)
            for user in TENANTS[tenant]['users']:
                self.client.tenants.add_user(user,
                                             tenant,
                                             TENANTS[tenant]['users'][user])
            for group in TENANTS[tenant]['groups']:
                self.client.tenants.add_user_group(
                    group, tenant, TENANTS[tenant]['groups'][group])

    def _can_perform_admin_only_action(self, client):
        try:
            client.maintenance_mode.deactivate()
        except ForbiddenError:
            return False
        except NotModifiedError:
            pass
        return True

    def _can_perform_user_action(self, client):
        try:
            client.maintenance_mode.status()
        except ForbiddenError:
            return False
        return True

    def test_authorization(self):
        for user in USERS:
            for tenant in TENANTS:
                self._test_user_in_tenant(user, tenant)

    def test_change_role(self):
        client = self.create_rest_client(
            username='u2',
            password='12345',
            tenant='t1'
        )

        assert self._can_perform_user_action(client)
        assert not self._can_perform_admin_only_action(client)

        self.client.users.set_role('u2', ADMIN_ROLE)
        assert self._can_perform_user_action(client)
        assert self._can_perform_admin_only_action(client)

        # back as a simple user, can't change ssl mode
        self.client.users.set_role('u2', USER_ROLE)
        assert self._can_perform_user_action(client)
        assert not self._can_perform_admin_only_action(client)

        # now adding the user to a new admins group, so it can change ssl mode
        self.client.user_groups.create('g5', ADMIN_ROLE)
        self.client.user_groups.add_user('u2', 'g5')
        assert self._can_perform_user_action(client)
        assert self._can_perform_admin_only_action(client)

    def _test_user_in_tenant(self, user, tenant):
        client = self.create_rest_client(
            username=user,
            password='12345',
            tenant=tenant
        )
        self._test_action(
            user,
            tenant,
            [ADMIN_ROLE, USER_ROLE],
            client.manager.get_status
        )
        self._test_action(
            user,
            tenant,
            [ADMIN_ROLE, TENANT_MANAGER, TENANT_USER, TENANT_VIEWER],
            client.blueprints.list
        )
        self._test_action(
            user,
            tenant,
            [ADMIN_ROLE, TENANT_MANAGER, TENANT_USER],
            client.secrets.create,  # any non-viewer action
            'secret1', 'value1',
            update_if_exists=True
        )
        self._test_action(
            user,
            tenant,
            [ADMIN_ROLE, TENANT_MANAGER, TENANT_USER],
            client.tenants.get,
            tenant
        )

    def _test_action(self, user, tenant, allowed_roles, func, *args, **kwargs):
        if _is_authorized(user, tenant, allowed_roles):
            func(*args, **kwargs)
        else:
            self.assertRaises(ForbiddenError, func, *args, **kwargs)

    def test_change_role_permissions(self):
        username, password = 'viewer_user', '12345'
        self.client.users.create(username, password, USER_ROLE)
        client = self.create_rest_client(
            username=username,
            password=password,
            tenant='default_tenant'
        )
        assert self._can_perform_user_action(client)
        self.client.permissions.delete('maintenance_mode_get', USER_ROLE)
        self.addCleanup(self.client.permissions.add,
                        'maintenance_mode_get', USER_ROLE)
        assert not self._can_perform_user_action(client)
