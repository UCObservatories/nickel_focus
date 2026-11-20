"""
Taking an exposure with the Nickel science camera (:class:`Exposure`).

The camera's settings and file paths are part of its KTL service; see
:class:`~nickel_focus.services.nscicam.NSciCam`.
"""
from nickel_focus import log
from nickel_focus.services.base import require


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

    FILE_TIMEOUT = 30
    """
    Seconds to wait, after an exposure ends, for the camera to report its
    finished file (``FITSFILE``).
    """

    def expose(self, record=None, speed=None, binning=None, exptime=None, object_name=None):
        """
        Configure the camera (see
        :meth:`~nickel_focus.services.nscicam.ExposureConfig.configure`), take
        one exposure, and wait for its file to be finished.

        The exposure is done once ``EXPSTATE`` returns to ``Ready`` *and*
        ``FITSFILE`` names a new file, which the camera sets only when the
        file, header included, is complete; its path is then
        :attr:`path.previous <nickel_focus.services.nscicam.ExposurePath.previous>`.

        Raises
        ------
        ValueError
            Raised if the camera isn't ready to start within 15 s, the
            exposure isn't seen to start within 30 s, the camera reports an
            error (``EXPSTATE == Error``; the message includes ``EXPRESULT``),
            the exposure doesn't end within the exposure time plus 90 s, or no
            new file is reported within :attr:`FILE_TIMEOUT` of its end.
        ~nickel_focus.services.base.KTLTimeoutError
            Raised if the camera service doesn't reply to one of these checks.
        """
        # Check that an exposure isn't currently happening
        require(
            self.cam.exposure_state_is('Ready', timeout=15),
            'Camera exposure state not ready. Cannot take exposure.',
            'No reply from nscicam.EXPSTATE; cannot tell whether the camera is ready.'
        )

        self.cfg.configure(
            record=record, speed=speed, binning=binning, exptime=exptime, object_name=object_name
        )

        # The file from the previous exposure; this exposure's will differ.
        previous_file = self.cam.fits_file()

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
        require(
            self.cam.exposure_state_is('Start', timeout=30),
            'Exposure start (EXPSTATE == Start) not detected within timeout',
            'No reply from nscicam.EXPSTATE; cannot tell whether the exposure started.'
        )

        # Then wait for it to end, successfully (Ready) or not (Error)
        require(
            self.cam.exposure_ended(timeout=round(float(self.cfg.exptime) + 90.)),
            'Exposure end (EXPSTATE == Ready) not detected within timeout',
            'No reply from nscicam.EXPSTATE; cannot tell whether the exposure finished.'
        )
        if self.cam.exposure_state().strip().lower() == 'error':
            raise ValueError(
                f'The camera reported an exposure error: {self.cam.exposure_result()}'
            )

        # Then wait for the finished file
        require(
            self.cam.fits_file_changed(previous_file, timeout=self.FILE_TIMEOUT),
            f'The camera did not report a new file (FITSFILE) within {self.FILE_TIMEOUT} s of '
            'the exposure ending.',
            'No reply from nscicam.FITSFILE; cannot tell whether the exposure was written.'
        )
        log.info('Exposure completed successfully')
