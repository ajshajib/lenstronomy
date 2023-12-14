__author__ = "sibirrer"

import numpy as np
from scipy.interpolate import interp1d
import lenstronomy.GalKin.velocity_util as vel_util
from lenstronomy.GalKin.cosmo import Cosmo
from lenstronomy.GalKin.anisotropy import Anisotropy
from lenstronomy.LensModel.Profiles.spp import SPP
import lenstronomy.Util.constants as const
import math
from lenstronomy.GalKin.light_profile import LightProfile
from copy import deepcopy

__all__ = ["AnalyticKinematics"]


class AnalyticKinematics(Anisotropy):
    """Class to compute eqn 20 in Suyu+2010 with a Monte-Carlo from rendering from the
    light profile distribution and displacing them with a Gaussian seeing convolution.

    This class assumes spherical symmetry in light and mass distribution and
        - a Hernquist light profile (parameterised by the half-light radius)
        - a power-law mass profile (parameterized by the Einstein radius and logarithmic slop)

    The analytic equations for the kinematics in this approximation are presented e.g. in Suyu et al. 2010 and
    the spectral rendering approach to compute the seeing convolved slit measurement is presented in Birrer et al. 2016.
    The stellar anisotropy is parameterised based on Osipkov 1979; Merritt 1985.

    WARNING!!! Only supports Osipkov-Merritt anisotropy for now!

    All units are meant to be in angular arc seconds. The physical units are fold in through the angular diameter
    distances
    """

    def __init__(
        self,
        kwargs_cosmo,
        interpol_grid_num=200,
        log_integration=True,
        max_integrate=100,
        min_integrate=1e-4,
    ):
        """

        :param kwargs_cosmo: keyword argument with angular diameter distances entering the Galkin.cosmo class
        :param interpol_grid_num: number of interpolations in radius to compute radial velocity dispersion
        :param log_integration: perform numerical integration in logarithmic space,
            setting False may lead to less accurate results
        :param max_integrate: maximum radius of integration (in projected arc seconds)
        :param min_integrate: minimum drawing/calculation of velocity dispersion (in projected arc seconds)
        """
        self._interp_grid_num = interpol_grid_num
        self._log_int = log_integration
        self._max_integrate = (
            max_integrate  # maximal integration (and interpolation) in units of arcsecs
        )
        self._min_integrate = (
            min_integrate  # min integration (and interpolation) in units of arcsecs
        )
        self._max_interpolate = max_integrate  # we chose to set the interpolation range to the integration range
        self._min_interpolate = min_integrate  # we chose to set the interpolation range to the integration range

        self._cosmo = Cosmo(**kwargs_cosmo)
        self._spp = SPP()

        self.light_profile = LightProfile(
            ["HERNQUIST"],
            interpol_grid_num=interpol_grid_num,
            max_interpolate=max_integrate,
            min_interpolate=min_integrate,
            max_draw=max_integrate,
        )
        Anisotropy.__init__(self, anisotropy_type="OM")

    @property
    def max_integrate(self):
        """Get the maximum range of integration."""
        return self._max_integrate

    @property
    def min_integrate(self):
        """Get the minimum range of integration."""
        return self._min_integrate

    def _rho0_r0_gamma(self, theta_E, gamma):
        # equation (14) in Suyu+ 2010
        return (
            -1
            * math.gamma(gamma / 2)
            / (np.sqrt(np.pi) * math.gamma((gamma - 3) / 2.0))
            * theta_E**gamma
            / self._cosmo.arcsec2phys_lens(theta_E)
            * self._cosmo.epsilon_crit
            * const.M_sun
            / const.Mpc**3
        )

    @staticmethod
    def draw_light(kwargs_light):
        """

        :param kwargs_light: keyword argument (list) of the light model
        :return: 3d radius (if possible), 2d projected radius, x-projected coordinate, y-projected coordinate
        """
        if "a" not in kwargs_light:
            if "Rs" in kwargs_light:
                a = kwargs_light["Rs"]
            elif "r_eff" in kwargs_light:
                a = 0.551 * kwargs_light["r_eff"]
            else:
                raise ValueError(
                    "Hernquist half-light radius can not be " "determined!"
                )
        else:
            a = kwargs_light["a"]

        r = vel_util.draw_hernquist(a)
        R, x, y = vel_util.project2d_random(r)
        return r, R, x, y

    def _sigma_s2(self, r, R, r_ani, a, gamma, rho0_r0_gamma):
        """Projected velocity dispersion :param r: 3d radius of the light tracer
        particle :param R: 2d projected radius of the light tracer particle :param
        r_ani: anisotropy radius :param a: scale of the Hernquist light profile :param
        gamma: power-law slope of the mass profile :param rho0_r0_gamma: combination of
        Einstein radius and power-law slope as equation (14) in Suyu+ 2010 :return:
        projected velocity dispersion."""
        beta = self.beta_r(r, **{"r_ani": r_ani})
        return (1 - beta * R**2 / r**2) * self._sigma_r2_interp(
            r, a, gamma, rho0_r0_gamma, r_ani
        )

    def sigma_s2(self, r, R, kwargs_mass, kwargs_light, kwargs_anisotropy):
        """Returns unweighted los velocity dispersion for a specified projected radius,
        with weight 1.

        :param r: 3d radius (not needed for this calculation)
        :param R: 2d projected radius (in angular units of arcsec)
        :param kwargs_mass: mass model parameters (following lenstronomy lens model
            conventions)
        :param kwargs_light: deflector light parameters (following lenstronomy light
            model conventions)
        :param kwargs_anisotropy: anisotropy parameters, may vary according to
            anisotropy type chosen. We refer to the Anisotropy() class for details on
            the parameters.
        :return: line-of-sight projected velocity dispersion at projected radius R from
            3d radius r
        """
        a, gamma, rho0_r0_gamma, r_ani = self._read_out_params(
            kwargs_mass, kwargs_light, kwargs_anisotropy
        )
        return self._sigma_s2(r, R, r_ani, a, gamma, rho0_r0_gamma), 1

    def sigma_r2(self, r, kwargs_mass, kwargs_light, kwargs_anisotropy):
        """Equation (19) in Suyu+ 2010.

        :param r: 3d radius
        :param kwargs_mass: mass profile keyword arguments
        :param kwargs_light: light profile keyword arguments
        :param kwargs_anisotropy: anisotropy keyword arguments
        :return: velocity dispersion in [m/s]
        """
        a, gamma, rho0_r0_gamma, r_ani = self._read_out_params(
            kwargs_mass, kwargs_light, kwargs_anisotropy
        )
        return self._sigma_r2(r, a, gamma, rho0_r0_gamma, r_ani)

    def _read_out_params(self, kwargs_mass, kwargs_light, kwargs_anisotropy):
        """Reads the relevant parameters out of the keyword arguments and transforms
        them to the conventions used in this class.

        :param kwargs_mass: mass profile keyword arguments
        :param kwargs_light: light profile keyword arguments
        :param kwargs_anisotropy: anisotropy keyword arguments
        :return: a (Rs of Hernquist profile), gamma, rho0_r0_gamma, r_ani
        """
        if "a" not in kwargs_light:
            if "Rs" in kwargs_light:
                a = kwargs_light["Rs"]
            elif "r_eff" in kwargs_light:
                a = 0.551 * kwargs_light["r_eff"]
            else:
                raise ValueError(
                    "Hernquist half-light radius can not be " "determined!"
                )
        else:
            a = kwargs_light["a"]

        if "rho0_r0_gamma" not in kwargs_mass:
            kwargs_mass["rho0_r0_gamma"] = self._rho0_r0_gamma(
                kwargs_mass["theta_E"], kwargs_mass["gamma"]
            )

        gamma = kwargs_mass["gamma"]
        rho0_r0_gamma = kwargs_mass["rho0_r0_gamma"]
        r_ani = kwargs_anisotropy["r_ani"]

        return a, gamma, rho0_r0_gamma, r_ani

    def _sigma_r2(self, r, a, gamma, rho0_r0_gamma, r_ani):
        """Equation (19) in Suyu+ 2010."""
        # first term
        prefac1 = 4 * np.pi * const.G * a ** (-gamma) * rho0_r0_gamma / (3 - gamma)
        prefac2 = r * (r + a) ** 3 / (r**2 + r_ani**2)
        # TODO check whether interpolation functions can speed this up
        hyp1 = vel_util.hyp_2F1(a=2 + gamma, b=gamma, c=3 + gamma, z=1.0 / (1 + r / a))
        hyp2 = vel_util.hyp_2F1(a=3, b=gamma, c=1 + gamma, z=-a / r)
        fac = r_ani**2 / a**2 * hyp1 / (
            (2 + gamma) * (r / a + 1) ** (2 + gamma)
        ) + hyp2 / (gamma * (r / a) ** gamma)
        return (
            prefac1 * prefac2 * fac * (const.arcsec * self._cosmo.dd * const.Mpc) ** 2
        )

    def _sigma_r2_interp(self, r, a, gamma, rho0_r0_gamma, r_ani):
        """

        :param r:
        :param a:
        :param gamma:
        :param rho0_r0_gamma:
        :param r_ani:
        :return:
        """
        if not hasattr(self, "_interp_sigma_r2"):
            min_log = np.log10(self._min_integrate)
            max_log = np.log10(self._max_integrate)
            r_array = np.logspace(min_log, max_log, self._interp_grid_num)
            I_R_sigma2_array = []
            for r_i in r_array:
                I_R_sigma2_array.append(
                    self._sigma_r2(r_i, a, gamma, rho0_r0_gamma, r_ani)
                )
            self._interp_sigma_r2 = interp1d(
                np.log(r_array), np.array(I_R_sigma2_array), fill_value="extrapolate"
            )
        return self._interp_sigma_r2(np.log(r))

    def _I_R_sigma2(self, R, kwargs_mass, kwargs_light, kwargs_anisotropy):
        """Equation A15 in Mamon&Lokas 2005 as a logarithmic numerical integral (if
        option is chosen)

        :param R: 2d projected radius (in angular units)
        :param kwargs_mass: mass model parameters (following lenstronomy lens model
            conventions)
        :param kwargs_light: deflector light parameters (following lenstronomy light
            model conventions)
        :param kwargs_anisotropy: anisotropy parameters, may vary according to
            anisotropy type chosen. See the Anisotropy() class for details on the
            parameters.
        :return: integral of A15 in Mamon&Lokas 2005
        """
        R = max(R, self._min_integrate)
        max_integrate = (
            self._max_integrate
        )  # make sure the integration of the Jeans equation is performed further out than the interpolation

        if self._log_int is True:
            min_log = np.log(R)
            max_log = np.log(max_integrate)
            dlogr = (max_log - min_log) / (self._interp_grid_num - 1)
            r_array = np.logspace(
                min_log + dlogr / 2.0,
                max_log + dlogr / 2.0,
                self._interp_grid_num,
                base=np.e,
            )
            dlog_r = np.log(r_array[2]) - np.log(r_array[1])
            IR_sigma2_ = self._integrand_suyu10_eq21(
                r_array, R, kwargs_mass, kwargs_light, kwargs_anisotropy
            )
            IR_sigma2_dr = IR_sigma2_ * dlog_r * r_array
        else:
            r_array = np.linspace(
                start=R, stop=self._max_interpolate, num=self._interp_grid_num
            )
            dr = r_array[2] - r_array[1]
            IR_sigma2_ = self._integrand_suyu10_eq21(
                r_array + dr / 2.0, R, kwargs_mass, kwargs_light, kwargs_anisotropy
            )
            IR_sigma2_dr = IR_sigma2_ * dr

        IR_sigma2 = 2 * np.sum(IR_sigma2_dr)  # integral from angle to

        kwargs_light_copy = deepcopy(kwargs_light)
        if "r_eff" in kwargs_light_copy:
            kwargs_light_copy["Rs"] = kwargs_light_copy["r_eff"] * 0.551
            kwargs_light_copy["amp"] = 1
            kwargs_light_copy.pop("r_eff")
        IR = self.light_profile.light_2d_finite(R, [kwargs_light_copy])

        return IR_sigma2, IR

    def _integrand_suyu10_eq21(
        self, r, R, kwargs_mass, kwargs_light, kwargs_anisotropy
    ):
        """Compute the integrand of Eq. 21 from Suyu et al. (2010).

        :param r: 3d radius (not needed for this calculation)
        :param R: 2d projected radius (in angular units of arcsec)
        :param kwargs_mass: mass model parameters (following lenstronomy lens model
            conventions)
        :param kwargs_light: deflector light parameters (following lenstronomy light
            model conventions)
        :param kwargs_anisotropy: anisotropy parameters, may vary according to
            anisotropy type chosen. We refer to the Anisotropy() class for details on
            the parameters.
        :return: integrand of Eq. 21 from Suyu et al. (2010)
        """
        a, gamma, rho0_r0_gamma, r_ani = self._read_out_params(
            kwargs_mass, kwargs_light, kwargs_anisotropy
        )
        kwargs_light_copy = deepcopy(kwargs_light)
        if "r_eff" in kwargs_light_copy:
            kwargs_light_copy["Rs"] = kwargs_light_copy["r_eff"] * 0.551
            kwargs_light_copy["amp"] = 1
            kwargs_light_copy.pop("r_eff")

        return (
            self._sigma_s2(r, R, r_ani, a, gamma, rho0_r0_gamma)
            * r
            / np.sqrt(r**2 - R**2)
            * self.light_profile.light_3d(r, [kwargs_light_copy])
        )

    def I_R_sigma2_and_IR(self, R, kwargs_mass, kwargs_light, kwargs_anisotropy):
        """Return I(R)*sigma^2 equation 20 in Suyu 2010 as interpolation in log space,
        and I(R)

        :param R: projected radius
        :param kwargs_mass: mass profile keyword arguments
        :param kwargs_light: light model keyword arguments
        :param kwargs_anisotropy: stellar anisotropy keyword arguments
        :return:
        """
        return self._I_R_sigma2(R, kwargs_mass, kwargs_light, kwargs_anisotropy)

    def grav_potential(self, r, kwargs_mass):
        """Gravitational potential in SI units.

        :param r: radius (arc seconds)
        :param kwargs_mass:
        :return: gravitational potential
        """
        theta_E = kwargs_mass["theta_E"]
        gamma = kwargs_mass["gamma"]
        mass_dimless = self._spp.mass_3d_lens(r, theta_E, gamma)
        mass_dim = (
            mass_dimless
            * const.arcsec**2
            * self._cosmo.dd
            * self._cosmo.ds
            / self._cosmo.dds
            * const.Mpc
            * const.c**2
            / (4 * np.pi * const.G)
        )
        grav_pot = -const.G * mass_dim / (r * const.arcsec * self._cosmo.dd * const.Mpc)
        return grav_pot

    def delete_cache(self):
        """Deletes temporary cache tight to a specific model.

        :return:
        """
        if hasattr(self, "_interp_sigma_r2"):
            del self._interp_sigma_r2
