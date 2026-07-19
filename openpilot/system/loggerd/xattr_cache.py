import errno

import xattr

_cached_attributes: dict[tuple, bytes | None] = {}

def getxattr(path: str, attr_name: str) -> bytes | None:
  key = (path, attr_name)
  if key not in _cached_attributes:
    try:
      response = xattr.getxattr(path, attr_name)
    except OSError as e:
      # ENODATA (Linux) or ENOATTR (macOS) means attribute hasn't been set.
      # EOPNOTSUPP means the filesystem has no xattr support at all (tmpfs, some
      # network mounts) -- there is no marker to read, which is the same answer as
      # "not set". Erroring instead would make an unmarked file look like a failure;
      # reporting None errs toward re-uploading, which is the safe direction.
      no_attr = (errno.ENODATA, getattr(errno, 'ENOATTR', errno.ENODATA),
                 errno.EOPNOTSUPP, getattr(errno, 'ENOTSUP', errno.EOPNOTSUPP))
      if e.errno in no_attr:
        response = None
      else:
        raise
    _cached_attributes[key] = response
  return _cached_attributes[key]

def setxattr(path: str, attr_name: str, attr_value: bytes) -> None:
  _cached_attributes.pop((path, attr_name), None)
  xattr.setxattr(path, attr_name, attr_value)
