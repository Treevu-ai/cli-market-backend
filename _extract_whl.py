import zipfile, os
whl = os.path.join(os.environ['TEMP'], 'core-check', 'cli_market_core-1.9.41-py3-none-any.whl')
out = os.path.join(os.environ['TEMP'], 'core-check', 'extracted_141')
os.makedirs(out, exist_ok=True)
z = zipfile.ZipFile(whl)
z.extract('market_core/market_db.py', out)
z.close()
print('extracted 1.9.41')
