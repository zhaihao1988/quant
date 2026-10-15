# Third-party provenance

Dependencies are listed in pyproject.toml and requirements.lock.txt. They retain their respective upstream licenses.

`src/quant/tdx/gbbq_key.hex` is the fixed TDX decoding key schedule, extracted as data from the previously inspected pytdx2 reader, not an executed dependency. Reference: https://github.com/liewhite/pytdx2/blob/master/pytdx/reader/gbbq_reader.py . No license claim is made here for the upstream repository: its license file could not be verified during this build. Parser implementation here is a new compact implementation based on the local audit, with binary structures cross-checked against https://github.com/mootdx/mootdx and https://github.com/injoyai/tdx .

The fixed key table is not an account secret. No local price, financial, account, or login files are redistributed with this repository.

Apache ECharts 6.0.0 is vendored under src/quant/static/vendor with its Apache-2.0 LICENSE and NOTICE. Source: https://github.com/apache/echarts/releases/tag/6.0.0 . Distribution: https://cdn.jsdelivr.net/npm/echarts@6.0.0/ . It is served locally without browser requests to a CDN.
