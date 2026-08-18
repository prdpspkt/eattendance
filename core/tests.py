"""Regression tests for the crashes found in the code audit.

Each test here exercises a path that previously raised.
"""
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Employee, Shift
from leaves.models import LeaveType
from travel_orders.models import TravelOrder

User = get_user_model()


class ViewRegressionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='rmaharjan', password='test-pass-123',
            first_name='Rita', last_name='Maharjan', role='OFFICE_ADMIN',
        )
        self.employee = Employee.objects.create(
            user=self.user, employee_id='EMP0001', join_date=date(2024, 1, 1),
        )
        self.leave_type = LeaveType.objects.create(name='Annual Leave', code='AL', days_per_year=20)
        self.client.force_login(self.user)

    def _travel_order(self, start, end):
        return TravelOrder.objects.create(
            employee=self.employee, destination='Pokhara', purpose='Site visit',
            start_date=timezone.make_aware(datetime.combine(start, time(9, 0))),
            end_date=timezone.make_aware(datetime.combine(end, time(17, 0))),
            status='APPROVED',
        )

    def test_leave_request_overlapping_a_travel_order_shows_an_error(self):
        """Previously raised TypeError: template filter syntax inside an f-string."""
        self._travel_order(date(2026, 4, 6), date(2026, 4, 8))

        response = self.client.post(reverse('request_leave'), {
            'leave_type': self.leave_type.id,
            'start_date': '2026-04-07',
            'end_date': '2026-04-09',
            'reason': 'Family event',
        })

        self.assertEqual(response.status_code, 200)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('already have a travel order' in m for m in messages), messages)
        self.assertTrue(any('06 Apr 2026' in m for m in messages), messages)

    def test_travel_order_overlapping_another_shows_an_error(self):
        """Same bug on the travel order form, where nothing caught the exception."""
        self._travel_order(date(2026, 4, 6), date(2026, 4, 8))

        response = self.client.post(reverse('request_travel_order'), {
            'travel_type': 'DOMESTIC',
            'destination': 'Biratnagar',
            'purpose': 'Audit',
            'start_date': '2026-04-07T09:00',
            'end_date': '2026-04-09T17:00',
        })

        self.assertEqual(response.status_code, 200)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('already have a travel order' in m for m in messages), messages)

    def test_attendance_calendar_renders_for_every_month(self):
        """February and the 30-day months raised ValueError building date(y, m, 31)."""
        for month in range(1, 13):
            with self.subTest(month=month):
                response = self.client.get(
                    reverse('attendance_calendar'), {'month': month, 'year': 2026}
                )
                self.assertEqual(response.status_code, 200)

    def test_unlinked_enrollments_page_is_reachable(self):
        """The view and template existed but had no URL entry."""
        response = self.client.get(reverse('devices:unlinked_enrollments'))

        self.assertEqual(response.status_code, 200)

    def test_shift_delete_confirmation_renders(self):
        """Rendered a template that did not exist."""
        shift = Shift.objects.create(
            name='Day Shift', start_time=time(9, 0), end_time=time(18, 0),
        )

        response = self.client.get(reverse('shift_delete', args=[shift.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Day Shift')

    def test_employee_can_be_created_without_a_password(self):
        """Fell through to User.objects.make_random_password(), removed in Django 5.1."""
        response = self.client.post(reverse('devices:employee_create'), {
            'username': 'bkhadka', 'email': 'bkhadka@example.com',
            'first_name': 'Bikash', 'last_name': 'Khadka',
            'role': 'EMPLOYEE', 'employee_id': 'EMP0002',
            'join_date': '2026-01-05', 'employment_status': 'ACTIVE',
            'password': '',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Employee.objects.filter(employee_id='EMP0002').exists())


class MessageTagTests(TestCase):
    def test_error_messages_map_to_the_bootstrap_danger_class(self):
        """messages.error produced class 'alert-error', which Bootstrap does not define."""
        from django.contrib.messages import constants

        from django.conf import settings

        self.assertEqual(settings.MESSAGE_TAGS[constants.ERROR], 'danger')


class TemplateSmokeTests(TestCase):
    """Every main page must render. Guards the shared base.html shell."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='admin1', password='test-pass-123',
            first_name='Asha', last_name='Rana', role='OFFICE_ADMIN', is_superuser=True,
        )
        Employee.objects.create(
            user=self.user, employee_id='EMP9001', join_date=date(2024, 1, 1),
        )
        self.client.force_login(self.user)

    def test_all_main_pages_render(self):
        names = [
            'dashboard', 'profile', 'change_password', 'my_attendance',
            'attendance_calendar', 'my_leaves', 'request_leave',
            'my_travel_orders', 'request_travel_order', 'shift_management',
            'shift_create', 'leave_requests_management', 'leave_type_management',
            'leave_type_create', 'travel_orders_management',
            'devices:device_list', 'devices:device_create', 'devices:employee_management',
            'devices:employee_create', 'devices:todays_attendance',
            'devices:unlinked_enrollments',
        ]
        for name in names:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200, f'{name} returned {response.status_code}')

    def test_login_page_renders_for_anonymous(self):
        response = Client().get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign in')

    def test_shell_has_accessibility_landmarks(self):
        response = self.client.get(reverse('dashboard'))
        body = response.content.decode()

        self.assertIn('skip-link', body)
        self.assertIn('aria-current="page"', body)
        self.assertIn('data-sidebar-toggle', body)
        self.assertIn('data-theme-toggle', body)


class PaginationTests(TestCase):
    """Pagination controls must render on the first and last page.

    page_obj.previous_page_number raises EmptyPage on page 1, and Django does
    not silence it, so an unguarded pagination link 500s the whole page. Only
    data large enough to paginate triggers it.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='pager', password='test-pass-123', role='OFFICE_ADMIN',
        )
        self.employee = Employee.objects.create(
            user=self.user, employee_id='EMP7001', join_date=date(2024, 1, 1),
        )
        self.client.force_login(self.user)

        from attendance.models import DailyAttendance
        start = date(2026, 1, 1)
        DailyAttendance.objects.bulk_create([
            DailyAttendance(employee=self.employee, date=start + timedelta(days=offset),
                            status='PRESENT', working_hours=8)
            for offset in range(60)
        ])

    def test_first_page_renders(self):
        response = self.client.get(reverse('my_attendance'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Next')

    def test_middle_and_last_pages_render(self):
        for page in (2, 3):
            with self.subTest(page=page):
                response = self.client.get(reverse('my_attendance'), {'page': page})
                self.assertEqual(response.status_code, 200)

    def test_filters_survive_pagination(self):
        response = self.client.get(reverse('my_attendance'), {
            'start_date': '2026-01-01', 'end_date': '2026-02-10', 'page': 2,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'start_date=2026-01-01')

    def test_date_filter_actually_filters(self):
        """The template's date inputs used to be ignored by the view entirely."""
        response = self.client.get(reverse('my_attendance'), {
            'start_date': '2026-01-01', 'end_date': '2026-01-10',
        })

        self.assertEqual(response.context['paginator'].count, 10)

    def test_employee_list_pagination_renders(self):
        response = self.client.get(reverse('devices:employee_management'))

        self.assertEqual(response.status_code, 200)
