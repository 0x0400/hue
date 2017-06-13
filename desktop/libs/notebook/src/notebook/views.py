#!/usr/bin/env python
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging

from django.core.urlresolvers import reverse
from django.db.models import Q
from django.shortcuts import redirect
from django.utils.translation import ugettext as _

from desktop.conf import USE_NEW_EDITOR
from desktop.lib.django_util import render, JsonResponse
from desktop.lib.exceptions_renderable import PopupException
from desktop.lib.json_utils import JSONEncoderForHTML
from desktop.models import Document2, Document
from desktop.views import serve_403_error

from metadata.conf import has_optimizer, has_navigator

from notebook.conf import get_interpreters
from notebook.connectors.base import Notebook, get_api
from notebook.connectors.spark_shell import SparkApi
from notebook.decorators import check_document_access_permission, check_document_modify_permission
from notebook.management.commands.notebook_setup import Command
from notebook.models import make_notebook


LOG = logging.getLogger(__name__)


def notebooks(request):
  if not SHOW_NOTEBOOKS.get():
    return serve_403_error(request)

  editor_type = request.GET.get('type', 'notebook')

  if editor_type != 'notebook':
    if USE_NEW_EDITOR.get():
      notebooks = [doc.to_dict() for doc in Document2.objects.documents(user=request.user).search_documents(types=['query-%s' % editor_type])]
    else:
      notebooks = [d.content_object.to_dict() for d in Document.objects.get_docs(request.user, Document2, qfilter=Q(extra__startswith='query')) if not d.content_object.is_history and d.content_object.type == 'query-' + editor_type]
  else:
    if USE_NEW_EDITOR.get():
      notebooks = [doc.to_dict() for doc in Document2.objects.documents(user=request.user).search_documents(types=['notebook'])]
    else:
      notebooks = [d.content_object.to_dict() for d in Document.objects.get_docs(request.user, Document2, qfilter=Q(extra='notebook')) if not d.content_object.is_history]

  return render('notebooks.mako', request, {
      'notebooks_json': json.dumps(notebooks, cls=JSONEncoderForHTML),
      'editor_type': editor_type
  })


@check_document_access_permission()
def notebook(request):
  if not SHOW_NOTEBOOKS.get():
    return serve_403_error(request)

  notebook_id = request.GET.get('notebook')

  is_yarn_mode = False
  try:
    from spark.conf import LIVY_SERVER_SESSION_KIND
    is_yarn_mode = LIVY_SERVER_SESSION_KIND.get()
  except:
    LOG.exception('Spark is not enabled')

  return render('notebook.mako', request, {
      'editor_id': notebook_id or None,
      'notebooks_json': '{}',
      'options_json': json.dumps({
          'languages': get_interpreters(request.user),
          'session_properties': SparkApi.get_properties(),
          'is_optimizer_enabled': has_optimizer(),
          'is_navigator_enabled': has_navigator(),
          'editor_type': 'notebook'
      }),
      'is_yarn_mode': is_yarn_mode,
  })


@check_document_access_permission()
def editor(request):
  editor_id = request.GET.get('editor')
  editor_type = request.GET.get('type', 'hive')

  if editor_id:  # Open existing saved editor document
    document = Document2.objects.get(id=editor_id)
    editor_type = document.type.rsplit('-', 1)[-1]

  return render('editor.mako', request, {
      'editor_id': editor_id or None,
      'notebooks_json': '{}',
      'options_json': json.dumps({
          'languages': [{"name": "%s SQL" % editor_type.title(), "type": editor_type}],
          'mode': 'editor',
          'is_optimizer_enabled': has_optimizer(),
          'is_navigator_enabled': has_navigator(),
          'editor_type': editor_type
      })
  })


def new(request):
  return notebook(request)


def browse(request, database, table):
  editor_type = request.GET.get('type', 'hive')

  snippet = {'type': editor_type}
  sql_select = get_api(request, snippet).get_select_star_query(snippet, database, table)

  editor = make_notebook(name='Browse', editor_type=editor_type, statement=sql_select, status='ready-execute')

  return render('editor.mako', request, {
      'notebooks_json': json.dumps([editor.get_data()]),
      'options_json': json.dumps({
          'languages': [{"name": "%s SQL" % editor_type.title(), "type": editor_type}],
          'mode': 'editor',
      }),
      'editor_type': editor_type,
  })


@check_document_access_permission()
def execute_and_watch(request):
  notebook_id = request.GET.get('editor', request.GET.get('notebook'))
  snippet_id = int(request.GET['snippet'])
  action = request.GET['action']
  destination = request.GET['destination']

  notebook = Notebook(document=Document2.objects.get(id=notebook_id)).get_data()
  snippet = notebook['snippets'][snippet_id]
  editor_type = snippet['type']

  api = get_api(request, snippet)

  if action == 'save_as_table':
    sql, success_url = api.export_data_as_table(notebook, snippet, destination)
    editor = make_notebook(name='Execute and watch', editor_type=editor_type, statement=sql, status='ready-execute', database=snippet['database'])
  elif action == 'insert_as_query':
    sql, success_url = api.export_large_data_to_hdfs(notebook, snippet, destination)
    editor = make_notebook(name='Execute and watch', editor_type=editor_type, statement=sql, status='ready-execute', database=snippet['database'])
  elif action == 'index_query':
    sql, success_url = api.export_data_as_table(notebook, snippet, destination, is_temporary=True, location='')
    editor = make_notebook(name='Execute and watch', editor_type=editor_type, statement=sql, status='ready-execute')

    sample = get_api(request, snippet).fetch_result(notebook, snippet, 0, start_over=True)

    from indexer.api3 import _index # Will ve moved to the lib in next commit
    from indexer.file_format import HiveFormat
    from indexer.fields import Field

    file_format = {
        'name': 'col',
        'inputFormat': 'query',
        'format': {'quoteChar': '"', 'recordSeparator': '\n', 'type': 'csv', 'hasHeader': False, 'fieldSeparator': '\u0001'},
        "sample": '',
        "columns": [
            Field(col['name'], HiveFormat.FIELD_TYPE_TRANSLATE.get(col['type'], 'string')).to_dict()
            for col in sample['meta']
        ]
    }

    job_handle = _index(request, file_format, destination, query=notebook['uuid'])
    return redirect(reverse('oozie:list_oozie_workflow', kwargs={'job_id': job_handle['handle']['id']}))
  else:
    raise PopupException(_('Action %s is unknown') % action)

  return render('editor.mako', request, {
      'notebooks_json': json.dumps([editor.get_data()]),
      'options_json': json.dumps({
          'languages': [{"name": "%s SQL" % editor_type.title(), "type": editor_type}],
          'mode': 'editor',
          'success_url': success_url
      }),
      'editor_type': editor_type,
  })


@check_document_modify_permission()
def delete(request):
  notebooks = json.loads(request.POST.get('notebooks', '[]'))

  ctr = 0
  for notebook in notebooks:
    doc2 = Document2.objects.get_by_uuid(user=request.user, uuid=notebook['uuid'], perm_type='write')
    doc = doc2._get_doc1()
    doc.can_write_or_exception(request.user)
    doc2.trash()
    ctr += 1

  return JsonResponse({'status': 0, 'message': _('Trashed %d notebook(s)') % ctr})


@check_document_access_permission()
def copy(request):
  notebooks = json.loads(request.POST.get('notebooks', '[]'))

  for notebook in notebooks:
    doc2 = Document2.objects.get_by_uuid(user=request.user, uuid=notebook['uuid'])
    doc = doc2._get_doc1()
    name = doc2.name + '-copy'
    doc2 = doc2.copy(name=name, owner=request.user)

    doc.copy(content_object=doc2, name=name, owner=request.user)

  return JsonResponse({})


@check_document_access_permission()
def download(request):
  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))
  file_format = request.POST.get('format', 'csv')

  return get_api(request, snippet).download(notebook, snippet, file_format)


def install_examples(request):
  response = {'status': -1, 'message': ''}

  if request.method == 'POST':
    try:
      Command().handle(user=request.user)
      response['status'] = 0
    except Exception, err:
      LOG.exception(err)
      response['message'] = str(err)
  else:
    response['message'] = _('A POST request is required.')

  return JsonResponse(response)


def upgrade_session_properties(request, notebook):
  # Upgrade session data if using old format
  data = notebook.get_data()

  for session in data.get('sessions', []):
    api = get_api(request, session)
    if 'type' in session and hasattr(api, 'upgrade_properties'):
      properties = session.get('properties', None)
      session['properties'] = api.upgrade_properties(session['type'], properties)

  notebook.data = json.dumps(data)
  return notebook
