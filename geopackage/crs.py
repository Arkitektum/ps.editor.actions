"""Coordinate reference systems pre-registered in every written GeoPackage.

Everyone who produces GeoPackage files for the model in more than one CRS needs
the same rows in ``gpkg_spatial_ref_sys``, so the writer registers the CRSs in
common Norwegian use up front instead of only the one the feature tables use.

The definitions are OGC WKT 1 (the GDAL dialect GeoPackage 1.x expects), generated
once with pyproj from the EPSG database and embedded here, so the writer still
needs nothing beyond the standard library.
"""

from __future__ import annotations

# (srs_id/EPSG code, EPSG name, description, WKT 1 definition)
NORWEGIAN_SRS: tuple[tuple[int, str, str, str], ...] = (
    (
        25832,
        'ETRS89 / UTM zone 32N',
        'EUREF89 UTM sone 32, 2d',
        (
            'PROJCS["ETRS89 / UTM zone 32N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25832"]]'
        ),
    ),
    (
        25833,
        'ETRS89 / UTM zone 33N',
        'EUREF89 UTM sone 33, 2d',
        (
            'PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",15],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25833"]]'
        ),
    ),
    (
        25835,
        'ETRS89 / UTM zone 35N',
        'EUREF89 UTM sone 35, 2d',
        (
            'PROJCS["ETRS89 / UTM zone 35N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",27],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25835"]]'
        ),
    ),
    (
        5972,
        'ETRS89 / UTM zone 32N + NN2000 height',
        'EUREF89 UTM sone 32, 2d + NN2000',
        (
            'COMPD_CS["ETRS89 / UTM zone 32N + NN2000 height",'
            'PROJCS["ETRS89 / UTM zone 32N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25832"]],VERT_CS["NN2000 height",'
            'VERT_DATUM["Norway Normal Null 2000",2005,AUTHORITY["EPSG","1096"]],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
            'AXIS["Gravity-related height",UP],AUTHORITY["EPSG","5941"]],'
            'AUTHORITY["EPSG","5972"]]'
        ),
    ),
    (
        5973,
        'ETRS89 / UTM zone 33N + NN2000 height',
        'EUREF89 UTM sone 33, 2d + NN2000',
        (
            'COMPD_CS["ETRS89 / UTM zone 33N + NN2000 height",'
            'PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",15],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25833"]],VERT_CS["NN2000 height",'
            'VERT_DATUM["Norway Normal Null 2000",2005,AUTHORITY["EPSG","1096"]],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
            'AXIS["Gravity-related height",UP],AUTHORITY["EPSG","5941"]],'
            'AUTHORITY["EPSG","5973"]]'
        ),
    ),
    (
        5975,
        'ETRS89 / UTM zone 35N + NN2000 height',
        'EUREF89 UTM sone 35, 2d + NN2000',
        (
            'COMPD_CS["ETRS89 / UTM zone 35N + NN2000 height",'
            'PROJCS["ETRS89 / UTM zone 35N",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",27],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG",'
            '"9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
            'AUTHORITY["EPSG","25835"]],VERT_CS["NN2000 height",'
            'VERT_DATUM["Norway Normal Null 2000",2005,AUTHORITY["EPSG","1096"]],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
            'AXIS["Gravity-related height",UP],AUTHORITY["EPSG","5941"]],'
            'AUTHORITY["EPSG","5975"]]'
        ),
    ),
    (
        3857,
        'WGS 84 / Pseudo-Mercator',
        'Web Mercator / Pseudo-Mercator',
        (
            'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",'
            'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
            'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4326"]],PROJECTION["Mercator_1SP"],'
            'PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],'
            'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],'
            'AXIS["Northing",NORTH],EXTENSION["PROJ4",'
            '"+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs"],'
            'AUTHORITY["EPSG","3857"]]'
        ),
    ),
    (
        3575,
        'WGS 84 / North Pole LAEA Europe',
        'North Pole LAEA Europe, basert på WGS84',
        (
            'PROJCS["WGS 84 / North Pole LAEA Europe",GEOGCS["WGS 84",'
            'DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
            'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
            'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",'
            '0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG",'
            '"4326"]],PROJECTION["Lambert_Azimuthal_Equal_Area"],'
            'PARAMETER["latitude_of_center",90],PARAMETER["longitude_of_center",'
            '10],PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","3575"]]'
        ),
    ),
    (
        4326,
        'WGS 84',
        'WGS84 Geografisk',
        (
            'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
            '298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
            'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",'
            '0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG",'
            '"4326"]]'
        ),
    ),
    (
        3035,
        'ETRS89-extended / LAEA Europe',
        'EUREF89 / ETRS89-LAEA Europe',
        (
            'PROJCS["ETRS89-extended / LAEA Europe",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],'
            'PROJECTION["Lambert_Azimuthal_Equal_Area"],'
            'PARAMETER["latitude_of_center",52],PARAMETER["longitude_of_center",'
            '10],PARAMETER["false_easting",4321000],PARAMETER["false_northing",'
            '3210000],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG",'
            '"3035"]]'
        ),
    ),
    (
        4258,
        'ETRS89',
        'EUREF 89 Geografisk (ETRS 89) 2d',
        (
            'GEOGCS["ETRS89",DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]]'
        ),
    ),
    (
        5942,
        'ETRS89 + NN2000 height',
        'EUREF89 Geografisk + NN2000',
        (
            'COMPD_CS["ETRS89 + NN2000 height",GEOGCS["ETRS89",'
            'DATUM["European_Terrestrial_Reference_System_1989",'
            'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
            'AUTHORITY["EPSG","6258"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG",'
            '"8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4258"]],VERT_CS["NN2000 height",'
            'VERT_DATUM["Norway Normal Null 2000",2005,AUTHORITY["EPSG","1096"]],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
            'AXIS["Gravity-related height",UP],AUTHORITY["EPSG","5941"]],'
            'AUTHORITY["EPSG","5942"]]'
        ),
    ),
)
