"""Real Noto/Adobe index and binary integration in a disposable FontHub archive.
No sync is triggered and the user's catalogue is never modified.
"""
import os,tempfile
with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA']=folder
    import app
    client=app.app.test_client()
    for source in ['noto','adobe']:
        rows=client.get('/api/search?scope=remote&source='+source).json
        assert rows['total']>0,(source,rows)
        row=rows['faces'][0]
        if source=='adobe':
            rows=client.get('/api/search?scope=remote&source=adobe&q=Source+Code+Pro').json
            row=rows['faces'][0]
        page=client.get('/sources/'+source+'/fonts/'+row['id'])
        assert page.status_code==200 and b'data-source="'+source.encode()+b'"' in page.data
        # Explicit single-artifact preview verifies the source-pinned download path.
        preview=client.get('/font/'+row['id'])
        assert preview.status_code==200,(source,preview.status_code)
        preview.close()  # send_file owns an OS handle; release it before temp cleanup.
        import hashlib,shutil
        paths=app.google_fonts.font_paths(app.GOOGLE_DATA,row,first=True)
        from unittest.mock import patch
        # Limit integration acquisition to the downloaded font; exercise the
        # real import endpoint without pulling every large family build.
        manifest=app.google_fonts.resolve(app.GOOGLE_DATA,row);manifest=dict(manifest,artifacts=manifest['artifacts'][:1])
        import font_downloads
        with patch.object(app.google_fonts,'resolve',return_value=manifest),patch.object(app.google_fonts,'font_paths',return_value=paths):
            imported=client.post('/api/sources/'+source+'/import/'+row['id'])
            while font_downloads.process(app.db,app.DATA,app.GOOGLE_DATA,app.google_fonts):pass
        assert imported.status_code==202, imported.json
        assert imported.json['queued']
        while app.process_import(): pass
        while app.process_analysis(): pass
        faces=client.get('/api/catalog').json['faces']
        assert any(face['source']==source for face in faces)
        print('PASS:',source,'remote search, page, explicit binary preview and source-qualified import',flush=True)
