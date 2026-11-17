"""
Tests for :mod:`slew`: :class:`slew.NickelTelescopePointing`, run on the real
service class over the fake ``ktl`` (see ``fake_ktl.py``), and
:func:`slew.find_nearest_target`.
"""
from astropy.coordinates import Angle, SkyCoord
import pytest

from nickel_focus import slew
from nickel_focus import starlist
from nickel_focus.services import NickelPoco


def _write_starlist(tmp_path, lines):
    """
    Write a small starlist file for a test to read via ``file=``.

    Parameters
    ----------
    tmp_path
        Directory to write the file into (typically pytest's ``tmp_path``
        fixture).
    lines
        The starlist's data lines (standard format; see
        `nickel_focus.starlist`), without trailing newlines.

    Returns
    -------
    pathlib.Path
        Path to the written file.
    """
    path = tmp_path / 'stars.txt'
    path.write_text('\n'.join(lines) + '\n')
    return path


class TestFindNearestTarget:
    """`slew.find_nearest_target`: target selection, independent of ktl."""

    def test_finds_closest_target(self, tmp_path):
        path = _write_starlist(tmp_path, [
            'StarA 01:00:00 +10:00:00 2000.0',
            'StarB 13:00:00 -20:00:00 2000.0',
        ])
        telescope_coo = SkyCoord(ra='01:00:01', dec='+10:00:01', unit=('hourangle', 'deg'))

        name, ra, dec = slew.find_nearest_target(telescope_coo, file=path)

        assert name == 'StarA', 'the closer of the two candidate targets should be selected'
        assert isinstance(ra, Angle), 'ra should be returned as an Angle, not a bare number'
        assert isinstance(dec, Angle), 'dec should be returned as an Angle, not a bare number'

    def test_returned_position_matches_the_selected_target(self, tmp_path):
        # Regression test: find_nearest_target once returned RA in degrees
        # (via the starlist Table) while NickelTelescopePointing.slew_to
        # treats a bare number as hours -- silently sending the telescope
        # to the wrong RA. Returning an Angle sidesteps that, since its
        # own unit is used regardless of what unit= a caller passes.
        path = _write_starlist(tmp_path, ['StarA 05:30:00 +20:15:00 2000.0'])
        telescope_coo = SkyCoord(ra='05:30:00', dec='+20:15:00', unit=('hourangle', 'deg'))
        expected_table = starlist.parse_starlist(path)

        _, ra, dec = slew.find_nearest_target(telescope_coo, file=path)

        assert ra.deg == pytest.approx(expected_table['ra'][0], abs=1e-10), (
            'the returned ra should carry the same degrees value as the source table, not '
            'degrees mislabeled as hours (or vice versa)'
        )
        assert dec.deg == pytest.approx(expected_table['dec'][0], abs=1e-10), (
            'the returned dec should carry the same degrees value as the source table'
        )

    def test_search_string_restricts_candidates(self, tmp_path):
        path = _write_starlist(tmp_path, [
            'Pointing01 05:00:00 +30:00:00 2000.0',
            'Focusing01 05:00:00 +30:00:00 2000.0',
        ])
        telescope_coo = SkyCoord(ra='05:00:00', dec='+30:00:00', unit=('hourangle', 'deg'))

        name, _, _ = slew.find_nearest_target(telescope_coo, obj_search_str='Focusing', file=path)

        assert name == 'Focusing01', (
            'obj_search_str should restrict the candidates to matching names, even though '
            'Pointing01 is an equally close non-match'
        )

    def test_search_string_matching_nothing_raises(self, tmp_path):
        path = _write_starlist(tmp_path, ['StarA 01:00:00 +10:00:00 2000.0'])
        telescope_coo = SkyCoord(ra='01:00:00', dec='+10:00:00', unit=('hourangle', 'deg'))

        with pytest.raises(ValueError, match='no object names containing'):
            slew.find_nearest_target(telescope_coo, obj_search_str='Nonexistent', file=path)

    def test_default_file_is_the_packaged_catalog(self):
        telescope_coo = SkyCoord(ra=10.0, dec=29.0, unit=('hourangle', 'deg'))

        name, ra, dec = slew.find_nearest_target(telescope_coo)

        assert name.startswith(('Pointing', 'Focusing')), (
            'with no file= given, the default packaged pointing/focus catalog should be searched'
        )
        assert isinstance(ra, Angle), 'ra should be an Angle for the default catalog too'


class TestNickelTelescopePointing:
    """`slew.NickelTelescopePointing`, over the simulated telescope."""

    def test_current(self, fake_telescope):
        fake_telescope.ra, fake_telescope.dec = 5.5, 30.25

        current = slew.NickelTelescopePointing(NickelPoco()).current

        assert current.ra.hour == pytest.approx(5.5), 'the current RA should be read in hours'
        assert current.dec.deg == pytest.approx(30.25), 'the current Dec should be read in degrees'

    def test_slew_writes_decimal_hours_and_degrees(self, fake_telescope):
        telescope = slew.NickelTelescopePointing(NickelPoco())

        telescope.slew_to(Angle('05:30:00', unit='hourangle'), Angle('+20:15:00', unit='deg'))

        assert fake_telescope.slew_calls == [(pytest.approx(5.5), pytest.approx(20.25))], \
            'the target should be written in decimal hours and degrees'
        assert fake_telescope.ra == pytest.approx(5.5), 'the simulated telescope should arrive'

    @pytest.mark.parametrize('flag,message', [
        ('movement_allowed', 'Telescope movement is disabled!'),
        ('tracking_on', 'Tracking is disabled!'),
        ('ready', 'Telescope is not ready to move to a new target!'),
        ('reaches_target', 'Telescope failed to make it to target within 5 min.'),
    ])
    def test_slew_failures(self, fake_telescope, flag, message):
        setattr(fake_telescope, flag, False)
        telescope = slew.NickelTelescopePointing(NickelPoco())

        with pytest.raises(ValueError, match=message):
            telescope.slew_to(5.5, 20.25)
        if flag != 'reaches_target':
            assert fake_telescope.slew_calls == [], 'a failed check must not command a slew'
