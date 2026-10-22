import unittest
import warnings
import numpy as np
from app.core.simulacao import densidade_weibull, sobrevivencia_weibull


class WeibullEstabilidade(unittest.TestCase):
    def test_cauda_extrema_sem_overflow_ou_nan(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error',RuntimeWarning)
            x = np.array([1.,2.,10.,1e100])
            f = densidade_weibull(x,500.,1.)
            s = sobrevivencia_weibull(x,500.,1.)
        np.testing.assert_allclose(f,[500/np.e,0.,0.,0.])
        np.testing.assert_allclose(s,[1/np.e,0.,0.,0.])

    def test_truncamento_com_sobrevivencia_underflow(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error',RuntimeWarning)
            f = densidade_weibull(np.array([29.,30.,30.01]),2.,1.,30.)
        np.testing.assert_allclose(f,[0.,60.,60.02*np.exp(-.6001)],rtol=1e-10)

    def test_broadcast_truncamento_opcional_e_origem(self):
        x = np.array([0.,1.,2.])
        f = densidade_weibull(x,np.array([[1.],[2.]]),2.,np.array([[np.nan],[1.]]))
        np.testing.assert_allclose(f[0],.5*np.exp(-x/2))
        np.testing.assert_allclose(f[1],[0.,.5, np.exp(-.75)])
        self.assertEqual(densidade_weibull(0.,2.,2.),0.)
        self.assertTrue(np.isinf(densidade_weibull(0.,.5,2.)))
        self.assertEqual(sobrevivencia_weibull(0.,2.,2.),1.)
