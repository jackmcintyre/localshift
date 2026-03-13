class TestPricingModule:
    def test_import(self):
        from custom_components.localshift.sensors.pricing import (
            EffectiveCheapPriceSensor,
        )

        assert EffectiveCheapPriceSensor is not None
