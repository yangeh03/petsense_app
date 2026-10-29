"""Bundle the offline map tools under server/map_tools for the existing receiver."""
import sys
import tarfile


def package(source, target):
    with tarfile.open(fileobj=source, mode='r|') as incoming:
        with tarfile.open(fileobj=target, mode='w|gz') as outgoing:
            for member in incoming:
                if member.name == 'tools/offline-map' or member.name.startswith('tools/offline-map/'):
                    member.name = 'server/map_tools' + member.name[len('tools/offline-map'):]
                elif not (member.name == 'server' or member.name.startswith('server/')):
                    continue
                stream = incoming.extractfile(member) if member.isfile() else None
                outgoing.addfile(member, stream)


if __name__ == '__main__':
    package(sys.stdin.buffer, sys.stdout.buffer)
