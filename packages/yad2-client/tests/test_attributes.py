from yad2_client.attributes import normalize_attributes


def test_observed_listing_option_ids_map_to_product_specific_attributes() -> None:
    attributes = normalize_attributes(
        [
            {
                "optionText": "כרטיס מסך",
                "attributeTextHeb": "סוג רכיב מחשב",
                "attributeTextEng": "Computer Component Type",
                "optionId": "13287",
            },
            {
                "optionText": "Nvidia - אנבידיה",
                "attributeTextHeb": "יצרן",
                "attributeTextEng": "manufacture",
                "optionId": "12538",
            },
        ],
        "285",
    )
    assert attributes[0].attribute_id == "155" and attributes[0].id_source == "catalog"
    assert attributes[0].name == "COMPUTER_COMPONENT_TYPE" and attributes[0].option_id == "13287"
    assert attributes[1].attribute_id == "24"
    assert attributes[1].raw_value == "Nvidia - אנבידיה"


def test_measurements_are_normalized_only_with_explicit_units_and_keep_ranges_raw() -> None:
    attributes = normalize_attributes(
        [
            {"attributeTextHeb": 'רוחב (ס"מ)', "optionText": "120"},
            {"attributeTextEng": "Length", "optionText": "1.5 m"},
            {"attributeTextHeb": 'רוחב (ס"מ)', "optionText": "120-140"},
            {"attributeTextEng": "Unknown", "optionText": "120"},
            {"attributeId": "999999", "optionText": "16 GB"},
        ],
        "6392",
    )
    assert attributes[0].numeric_value == 120 and attributes[0].unit == "cm"
    assert attributes[1].numeric_value == 150 and attributes[1].raw_value == "1.5 m"
    assert attributes[2].numeric_value is None and attributes[2].raw_value == "120-140"
    assert attributes[3].attribute_id is None and attributes[3].numeric_value is None
    assert attributes[4].attribute_id == "999999" and attributes[4].id_source == "listing"
    assert attributes[4].numeric_value == 16 and attributes[4].unit == "GB"


def test_english_units_decimal_storage_and_conflicting_labels() -> None:
    attributes = normalize_attributes(
        [
            {"attributeTextEng": "Width (cm)", "optionText": 12.5},
            {"attributeTextEng": "Storage", "optionText": "2 TB"},
            {"attributeTextEng": "Clock", "optionText": "3.2 GHz"},
            {"attributeTextEng": "Width (m)", "attributeTextHeb": 'רוחב (ס"מ)', "optionText": "12"},
        ],
        None,
    )
    assert [(entry.numeric_value, entry.unit) for entry in attributes] == [
        (12.5, "cm"),
        (2000, "GB"),
        (3200, "MHz"),
        (None, None),
    ]
