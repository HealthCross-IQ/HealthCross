from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type


def test_maps_known_network_types_to_the_rate_card_names():
    assert map_network_type("PLATINUM") == "MSH Platinum"
    assert map_network_type("Comprehensive") == "MSH Comprehensive"
    assert map_network_type("premium") == "MSH Premium"
    assert map_network_type("Enhanced") == "MSH Enhanced"
    assert map_network_type("REGULAR") == "MSH Regular"


def test_essential_maps_to_platinum_per_underwriting():
    assert map_network_type("Essential") == "MSH Platinum"


def test_unrecognized_network_type_returns_none():
    assert map_network_type("Some Unknown Tier") is None
    assert map_network_type(None) is None
    assert map_network_type("") is None


def test_msh_intl_network_is_flagged_out_of_scope_not_mapped():
    assert map_network_type("MSH INTL NETWORK") is None
    assert is_out_of_scope_network_type("MSH INTL NETWORK") is True
    assert is_out_of_scope_network_type("msh intl network") is True


def test_in_scope_network_types_are_not_flagged_out_of_scope():
    assert is_out_of_scope_network_type("Platinum") is False
    assert is_out_of_scope_network_type(None) is False
