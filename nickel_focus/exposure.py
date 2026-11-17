"""
Interface to the Nickel science camera's exposure control via KTL: where
exposures are written (:class:`ExposurePath`), how they're configured
(:class:`ExposureConfig`), and taking one (:class:`Exposure`).
"""
from pathlib import Path

from nickel_focus import ktl
from nickel_focus import log


class ExposurePath:
    """
    KTL object used to read and set the paths for the image exposures.
    """
    def __init__(self):
        self.exprec = ktl.cache('nscicam', 'RECORD')
        self.expresult = ktl.cache('nscicam', 'EXPRESULT')
        self.scratchdir = ktl.cache('nscicam', 'SCRATCHDIR')
        self.recorddir = ktl.cache('nscicam', 'RECORDDIR')
        self.prefix = ktl.cache('nscicam', 'FITSPREFIX')
        self.obsnum = ktl.cache('nscicam', 'OBSNUM')
        self.suffix = ktl.cache('nscicam', 'FITSSUFFIX')

    @property
    def previous(self):
        return Path(self.expresult.read())

    @property
    def next(self):
        return self.for_obsnum(self.obsnum.read())

    def for_obsnum(self, obsnum, assume_recorded=False):
        # TODO: Is this the correct way to check the keyword has a given value?
        record = self.exprec.read() == 'Yes'
        if record or assume_recorded:
            path = Path(self.recorddir.read()).absolute()
        else:
            path = Path(self.scratchdir.read()).absolute()
        return path / f'{self.prefix.read()}{obsnum}{self.suffix.read()}'
    

class ExposureConfig:
    # Map user-facing speed names (e.g., from the GUI or CLI) to the AMPCONF
    # values accepted by the camera.  Names not in this map are passed through
    # unchanged and validated against AMPCONFLIST.
    speed_aliases = {
        'Fast': '1.0MHz',
        'Slow': '0.05MHz',
        }

    def __init__(self):
        self.exprec = ktl.cache('nscicam', 'RECORD')
        self._exptime = ktl.cache('nscicam', 'EXPOSURE')
        # NOTE: AMPCONFLIST gives the options
        self.expspeed  = ktl.cache('nscicam', 'AMPCONF')
        self.expspeed_options = ktl.cache('nscicam', 'AMPCONFLIST').read().split(',')
        # TODO: Is there a way to programmatically get the binning options
        self.expbin  = ktl.cache('nscicam', 'BINNING')
        self.expbin_options = ['1,1', '2,2', '4,4']

    def configure(self, record=None, speed=None, binning=None, exptime=None):
        if record is not None:
            self.exprec.write(record)
        if speed is not None:
            speed = self.speed_aliases.get(speed, speed)
            if speed not in self.expspeed_options:
                raise ValueError(
                    f'{speed} is not a valid read speed.  Must be one of {self.expspeed_options}.'
                )
            self.expspeed.write(speed)
        if binning is not None:
            if binning not in self.expbin_options:
                raise ValueError(
                    f'{binning} is not a valid binning.  Must be one of {self.expbin_options}.'
                )
            self.expbin.write(binning)
        if exptime is not None:
            self._exptime.write(exptime)

    @property
    def exptime(self):
        return self._exptime.read()

    def current_settings(self):
        """
        Read the camera's current exposure time, read speed, and binning.

        The read speed is reported by its user-facing name (a key of
        :attr:`speed_aliases`, e.g. ``'Fast'``) when the camera's ``AMPCONF``
        value has one, so the result uses the same names :meth:`configure`
        accepts; otherwise the raw ``AMPCONF`` value is returned.

        Returns
        -------
        :obj:`dict`
            Keys ``exptime`` (:obj:`float`, seconds), ``speed``, and
            ``binning`` (:obj:`str`, e.g. ``'2,2'``).
        """
        speed = self.expspeed.read().strip()
        speed_names = {value: name for name, value in self.speed_aliases.items()}
        return {
            'exptime': float(self._exptime.read()),
            'speed': speed_names.get(speed, speed),
            'binning': self.expbin.read().strip(),
        }

    def __repr__(self):
        return (
            'Exposure settings:\n'
            f'    Record: {self.exprec.read()}\n'
            f'    Time: {self.exptime}\n'
            f'    Speed: {self.expspeed.read()}\n'
            f'    Binning: {self.expbin.read()}\n'
        )
    
class Exposure:

    def __init__(self):
        if ktl is None:
            raise RuntimeError(
                'The ktl package is not available.  Taking new exposures requires a kpython '
                'environment with ktl installed.'
            )
        self.path = ExposurePath()
        self.cfg = ExposureConfig()

        # SCICAM exposure keywords
        self.expstate = ktl.cache('nscicam', 'EXPSTATE')
        self.expstate.monitor()

        self.expstart = ktl.cache('nscicam', 'EXPOSE')
        self.expstart.monitor()

    def expose(self, record=None, speed=None, binning=None, exptime=None):

        # Check that an exposure isn't currently happening
        if not self.expstate.waitFor('== Ready', timeout=15):
            raise ValueError('Camera exposure state not ready. Cannot take exposure.')

        self.cfg.configure(record=record, speed=speed, binning=binning, exptime=exptime)

        # Start the exposure
        # TODO: This needs to be confirmed once Will settles on the naming
        # Changed this to "Go" instead of startX because of the mode difference between Start and StartX
        self.expstart.write('Go')

        # RA:  "POCGDIX in rad"
        # DEC: "POCGDIY in rad"
        #   POCGDFIN = 1, write offsets, wait for GDFIN to be 0 or -1
        # POCMVFIN = 1 , wait for MVFIN to be 0 or -1
        # ghow -s poco c:w -fh
        # ghow -h
        # 
        # either a new target 

        # Daytime testing

        # Check that telescope is settled

        # Wait for it to start
        if not self.expstate.waitFor('== Start', timeout=30):
            raise ValueError('Exposure start (EXPSTATE == Start) not detected within timeout')

        # Then wait for it to be ready again
        if self.expstate.waitFor('== Ready', timeout=round(float(self.cfg.exptime) + 90.)):
            log.info('Exposure completed successfully')
        else:
            raise ValueError('Exposure EXPSTATE=Ready not detected within timeout')
