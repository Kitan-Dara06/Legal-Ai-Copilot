"""
Unit tests for app.models.UserRole.meets_threshold.

Validates the RBAC hierarchy:
    SYSTEM_ADMIN (100) > PARTNER (80) > ADMIN (70) > ASSOCIATE (50) > MEMBER (30)
"""

import pytest

from app.models import UserRole


class TestUserRoleMeetsThreshold:
    """Suite for UserRole.meets_threshold — pure enum logic, no I/O."""

    # ── SYSTEM_ADMIN: highest role, must meet all thresholds ──────────

    def test_system_admin_can_do_anything(self):
        """SYSTEM_ADMIN must meet the MEMBER threshold."""
        assert UserRole.SYSTEM_ADMIN.meets_threshold(UserRole.MEMBER) is True

    def test_system_admin_can_system_admin(self):
        """SYSTEM_ADMIN must meet its own threshold."""
        assert UserRole.SYSTEM_ADMIN.meets_threshold(UserRole.SYSTEM_ADMIN) is True

    def test_system_admin_can_partner(self):
        """SYSTEM_ADMIN must meet the PARTNER threshold."""
        assert UserRole.SYSTEM_ADMIN.meets_threshold(UserRole.PARTNER) is True

    def test_system_admin_can_admin(self):
        """SYSTEM_ADMIN must meet the ADMIN threshold."""
        assert UserRole.SYSTEM_ADMIN.meets_threshold(UserRole.ADMIN) is True

    def test_system_admin_can_associate(self):
        """SYSTEM_ADMIN must meet the ASSOCIATE threshold."""
        assert UserRole.SYSTEM_ADMIN.meets_threshold(UserRole.ASSOCIATE) is True

    # ── PARTNER (80) ──────────────────────────────────────────────────

    def test_partner_can_approve(self):
        """PARTNER must meet its own threshold."""
        assert UserRole.PARTNER.meets_threshold(UserRole.PARTNER) is True

    def test_partner_can_do_associate_work(self):
        """PARTNER must meet the ASSOCIATE threshold."""
        assert UserRole.PARTNER.meets_threshold(UserRole.ASSOCIATE) is True

    def test_partner_can_do_admin_work(self):
        """PARTNER (80) must meet the ADMIN (70) threshold."""
        assert UserRole.PARTNER.meets_threshold(UserRole.ADMIN) is True

    def test_partner_can_view_as_member(self):
        """PARTNER must meet the MEMBER threshold."""
        assert UserRole.PARTNER.meets_threshold(UserRole.MEMBER) is True

    def test_partner_cannot_system_admin(self):
        """PARTNER must NOT meet the SYSTEM_ADMIN threshold."""
        assert UserRole.PARTNER.meets_threshold(UserRole.SYSTEM_ADMIN) is False

    # ── ADMIN (70) ────────────────────────────────────────────────────

    def test_admin_can_view_as_member(self):
        """ADMIN must meet the MEMBER threshold."""
        assert UserRole.ADMIN.meets_threshold(UserRole.MEMBER) is True

    def test_admin_can_do_associate_work(self):
        """ADMIN must meet the ASSOCIATE threshold."""
        assert UserRole.ADMIN.meets_threshold(UserRole.ASSOCIATE) is True

    def test_admin_can_self(self):
        """ADMIN must meet its own threshold."""
        assert UserRole.ADMIN.meets_threshold(UserRole.ADMIN) is True

    def test_admin_cannot_approve_as_partner(self):
        """ADMIN (70) must NOT meet the PARTNER (80) threshold."""
        assert UserRole.ADMIN.meets_threshold(UserRole.PARTNER) is False

    def test_admin_cannot_system_admin(self):
        """ADMIN must NOT meet the SYSTEM_ADMIN threshold."""
        assert UserRole.ADMIN.meets_threshold(UserRole.SYSTEM_ADMIN) is False

    # ── ASSOCIATE (50) ────────────────────────────────────────────────

    def test_associate_can_view_as_member(self):
        """ASSOCIATE must meet the MEMBER threshold."""
        assert UserRole.ASSOCIATE.meets_threshold(UserRole.MEMBER) is True

    def test_associate_can_self(self):
        """ASSOCIATE must meet its own threshold."""
        assert UserRole.ASSOCIATE.meets_threshold(UserRole.ASSOCIATE) is True

    def test_associate_cannot_admin(self):
        """ASSOCIATE (50) must NOT meet the ADMIN (70) threshold."""
        assert UserRole.ASSOCIATE.meets_threshold(UserRole.ADMIN) is False

    def test_associate_cannot_approve_as_partner(self):
        """ASSOCIATE must NOT meet the PARTNER threshold."""
        assert UserRole.ASSOCIATE.meets_threshold(UserRole.PARTNER) is False

    def test_associate_cannot_system_admin(self):
        """ASSOCIATE must NOT meet the SYSTEM_ADMIN threshold."""
        assert UserRole.ASSOCIATE.meets_threshold(UserRole.SYSTEM_ADMIN) is False

    # ── MEMBER (30): lowest role ──────────────────────────────────────

    def test_member_can_view(self):
        """MEMBER must meet its own threshold."""
        assert UserRole.MEMBER.meets_threshold(UserRole.MEMBER) is True

    def test_member_cannot_approve_as_partner(self):
        """MEMBER must NOT meet the PARTNER threshold."""
        assert UserRole.MEMBER.meets_threshold(UserRole.PARTNER) is False

    def test_member_cannot_associate(self):
        """MEMBER (30) must NOT meet the ASSOCIATE (50) threshold."""
        assert UserRole.MEMBER.meets_threshold(UserRole.ASSOCIATE) is False

    def test_member_cannot_admin(self):
        """MEMBER must NOT meet the ADMIN threshold."""
        assert UserRole.MEMBER.meets_threshold(UserRole.ADMIN) is False

    def test_member_cannot_system_admin(self):
        """MEMBER must NOT meet the SYSTEM_ADMIN threshold."""
        assert UserRole.MEMBER.meets_threshold(UserRole.SYSTEM_ADMIN) is False

    # ── Hierarchy order verification ──────────────────────────────────

    def test_hierarchy_order_is_correct(self):
        """Ensure the hierarchy dict has the expected strict descending order."""
        hier = UserRole.hierarchy()
        assert hier[UserRole.SYSTEM_ADMIN] > hier[UserRole.PARTNER]
        assert hier[UserRole.PARTNER] > hier[UserRole.ADMIN]
        assert hier[UserRole.ADMIN] > hier[UserRole.ASSOCIATE]
        assert hier[UserRole.ASSOCIATE] > hier[UserRole.MEMBER]

    def test_hierarchy_values_are_unique(self):
        """Each role must have a distinct permission level (no ties)."""
        values = list(UserRole.hierarchy().values())
        assert len(values) == len(set(values)), "Hierarchy values must be unique"
