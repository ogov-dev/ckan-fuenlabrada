from setuptools import setup, find_namespace_packages

setup(
    name="ckanext-ogovtech_fixes",
    version="0.1.0",
    description="Ajustes técnicos comunes de la baseline CKAN de oGov.tech (esquema y perfil DCAT-AP-ES)",
    license="AGPL-3.0-or-later",
    packages=find_namespace_packages(include=["ckanext.*"]),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "ckan.plugins": [
            "ogovtech_fixes = ckanext.ogovtech_fixes.plugin:OgovtechFixesPlugin",
        ],
        "ckan.rdf.profiles": [
            "dcat_ap_es = ckanext.ogovtech_fixes.profiles:DCATAPESProfile",
        ],
    },
)
