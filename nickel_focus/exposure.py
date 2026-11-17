"""
Taking an exposure with the Nickel science camera (:class:`Exposure`).

The camera's settings and file paths are part of its KTL service; see
:class:`~nickel_focus.services.nscicam.NSciCam`.
"""
from nickel_focus import log


class Exposure:
    """
    Take exposures with the science camera.

    Parameters
    ----------
    cam : :class:`~nickel_focus.services.nscicam.NSciCam`
        The camera service.

    Attributes
    ----------
    cam : :class:`~nickel_focus.services.nscicam.NSciCam`
        The camera service.
    path : :class:`~nickel_focus.services.nscicam.ExposurePath`
        The camera's file paths (``cam.path``).
    cfg : :class:`~nickel_focus.services.nscicam.ExposureConfig`
        The camera's exposure settings (``cam.cfg``).
    """

    def __init__(self, cam):
        self.cam = cam
        self.path = cam.path
        self.cfg = cam.cfg

    def expose(self, record=None, speed=None, binning=None, exptime=None):
        """
        Configure the camera (see
        :meth:`~nickel_focus.services.nscicam.ExposureConfig.configure`), take
        one exposure, and wait for it to finish.

        Raises
        ------
        ValueError
            Raised if the camera isn't ready to start within 15 s, the
            exposure isn't seen to start within 30 s, or it doesn't finish
            within the exposure time plus 90 s.
        """
        # Check that an exposure isn't currently happening
        if not self.cam.exposure_state_is('Ready', timeout=15):
            raise ValueError('Camera exposure state not ready. Cannot take exposure.')

        self.cfg.configure(record=record, speed=speed, binning=binning, exptime=exptime)

        # Start the exposure
        # TODO: This needs to be confirmed once Will settles on the naming
        # Changed this to "Go" instead of startX because of the mode difference between Start and StartX
        self.cam.start_exposure()

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
        if not self.cam.exposure_state_is('Start', timeout=30):
            raise ValueError('Exposure start (EXPSTATE == Start) not detected within timeout')

        # Then wait for it to be ready again
        if self.cam.exposure_state_is('Ready', timeout=round(float(self.cfg.exptime) + 90.)):
            log.info('Exposure completed successfully')
        else:
            raise ValueError('Exposure EXPSTATE=Ready not detected within timeout')
