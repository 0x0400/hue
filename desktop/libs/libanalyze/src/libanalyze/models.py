import json
from itertools import groupby

class Contributor(object):
  def __init__(self, **kwargs):
    self.id = None
    self.query_id = None
    self.type = None
    self.wall_clock_time = None
    self.plan_node_id = None
    self.plan_node_name = None
    self.reason = None
    self.__dict__.update(kwargs)
    
  def to_json(self):
    return json.dumps(self.__dict__)

class Reason(object):
  def __init__(self, **kwargs):
    self.message = None
    self.impact = None
    self.__dict__.update(kwargs)
    
  def to_json(self):
    return json.dumps(self.__dict__)

class TCounter(object):
  def __init__(self, **kwargs):
    self.value = None
    self.name = None
    self.unit = None
    self.__dict__.update(kwargs)

def query_node_by_id(profile, node_id, metric_name, averaged=False):
  """Given the query_id, searches for the corresponding query profile and
  selects the node instances given by node_id, selects the metric given by
  metric_name and groups by fragment and fragment instance."""
  result = profile.find_by_id(node_id)
  if not result:
    return result
  nodes = filter(lambda x: x.fragment.is_averaged() == averaged, result)
  #Metric.value, Metric.unit, Fragment.id.label("fragment_id"), Fragment.fid, Fragment.host, Node.id.label("node_id"), Node.nid, Node.name
  metric = reduce(lambda x, y: x + y.find_metric_by_name(metric_name), nodes, [])
  
  return map(lambda x: L(x['value'], x['unit'], 0, x['node'].fragment.id(), x['node'].host(), 0, x['node'].id(), x['node'].name(), value=x['value'], unit=x['unit'], fragment_id=0, fid=x['node'].fragment.id(), host=x['node'].host(), node_id=x['node'].id(), name=x['node'].name(), node=x['node']), metric)

def query_node_by_metric(profile, node_name, metric_name):
  """Given the query_id, searches for the corresponding query profile and
  selects the node instances given by node_name, selects the metric given by
  metric_name and groups by fragment and fragment instance."""

  result = profile.find_all_by_name(node_name)
  nodes = filter(lambda x: x.fragment.is_averaged() == False, result)
  metric = reduce(lambda x, y: x + y.find_metric_by_name(metric_name), nodes, [])
  return map(lambda x: L(x['value'], 0, x['node'].fragment.id(), x['node'].host(), 0, x['node'].id(), x['node'].name(), value=x['value'], unit=x['unit'], fragment_id=0, fid=x['node'].fragment.id(), host=x['node'].host(), node_id=x['node'].id(), name=x['node'].name(), node=x['node']), metric)

def query_avg_fragment_metric_by_node_nid(profile, node_nid, metric_name):
  """
  Given the surragate node id (i.e. unique id of the plan node in the database),
  return the value of the fragment level metric.
  :param node_id:
  :param metric_name:
  :return: the value of the metric; none if there is no result
  """
  result = profile.find_by_id(node_nid)
  if not result:
    return result
  node = map(lambda x: x, filter(lambda x: x.fragment.is_averaged() == True, result))[0]
  metric = node.fragment.find_metric_by_name(metric_name)
  return metric[0]['value']

def query_fragment_metric_by_node_id(node, metric_name):
  """
  Given the surragate node id (i.e. unique id of the plan node in the database),
  return the value of the fragment level metric.
  :param node_id:
  :param metric_name:
  :return: the value of the metric; none if there is no result
  """
  metrics = node.find_metric_by_name(metric_name)
  return metrics[0]['value'] if metrics else None
  
def query_unique_node_by_id(profile, fragment_id, fragment_instance_id, node_id):
  result = profile.find_by_id(node_id)
  nodes = filter(lambda x: ((x.fragment is None and x.is_fragment()) or x.fragment.id() == fragment_id) and x.fragment_instance.id() == fragment_instance_id, result)
  return nodes[0]

def host_by_metric(profile, metric_name, exprs=[max]):
  """Queries all fragment instances for a particular associated metric value.
  Calculates the aggregated value based on exprs."""
  fragments = profile.find_all_fragments()
  fragments = filter(lambda x: x.is_averaged() == False, fragments)
  metrics = reduce(lambda x,y: x + y.find_metric_by_name(metric_name), fragments, [])
  results = []
  for k, g in groupby(metrics, lambda x: x['node'].host()):
      grouped = list(g)
      values = map(lambda x: x['value'], grouped)
      result = [k]
      for expr in exprs:
        value = expr(values)
        result.append(value)
      results.append(result)
  
  return results

class L(list):
  def __new__(self, *args, **kwargs):
      return super(L, self).__new__(self, args, kwargs)

  def __init__(self, *args, **kwargs):
      if len(args) == 1 and hasattr(args[0], '__iter__'):
          list.__init__(self, args[0])
      else:
          list.__init__(self, args)
      self.__dict__.update(kwargs)

  def __call__(self, **kwargs):
      self.__dict__.update(kwargs)
      return self