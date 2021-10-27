# KIELER - Kiel Integrated Environment for Layout Eclipse RichClient
# http://www.informatik.uni-kiel.de/rtsys/kieler/
#
# Copyright 2019 by
# + Kiel University
#   + Department of Computer Science
#     + Real-Time and Embedded Systems Group
#
# This code is provided under the terms of the Eclipse Public License (EPL).
# See the file epl-v10.html for the license text.

#
# This script bundles a self-contained eclipse update site into an executable uber-jar (no shading!) and several platform specific executable scripts bundling the jar.
# Author: Alexander Schulz-Rosengarten <als@informatik.uni-kiel.de>
#

# General Documentation:
# The idea of having an executable non-eclipse application with eclipse plugins is to bundle them into a uber/fat jar containing all dependencies.
# Usually this job is done by the maven-shade-plugin. However, since we build our plugins with tycho and have a defined target platform for dependency resolution, this system is incompatible with maven-shade. maven-shade would require to redefine each dependency explicitly and from a maven repository.
# Hence, we build a self-contained update side using tycho, this way we have all our dependencies automatically and correctly resolve without any redefinitions and we can build only specific plugins into the executable.
# Then this script bundles the jars in the update side into one uber jar.
# In contrast to maven-shade this script cannot 'shade' dependencies. If this should be necessary in the future, it might be a solution to process the update side by another build run using maven-shade and some pom file generated by a script to correctly defining the dependencies to include.

from __future__ import print_function # python3 print

import os
import stat
import sys
import shutil
import argparse
import re
from subprocess import check_call
from fnmatch import fnmatch
from os.path import isfile, isdir, join, abspath, relpath, dirname, basename

IGNORED_JARS = [
    'org.apache.ant*'
]
IGNORE_NESTED_JARS = [
]
IGNORED_FILES = [
    'org/*/*.java',
    'com/*/*.java',
    'de/*/*.java',
    'module-info.class',
    '*._trace',
    '*.g',
    '*.mwe2',
    '*.xtext',
    '*.genmodel',
    '*.ecore',
    '*.ecorediag',
    '*.html',
    '*.profile',
    '.api_description',
    '.options',
    'log4j.properties',
    'about.*',
    'about_*',
    'about_files/*',
    'cheatsheets/*',
    'META-INF/*.DSA',
    'META-INF/*.RSA',
    'META-INF/*.SF',
    'META-INF/changelog.txt',
    'META-INF/DEPENDENCIES',
    'META-INF/eclipse.inf',
    'META-INF/INDEX.LIST',
    'META-INF/MANIFEST.MF', # not sure if we have to merge the content somehow?
    'META-INF/maven/*',
    'META-INF/NOTICE.txt',
    'META-INF/NOTICE',
    'META-INF/p2.inf',
    'OSGI-INF/l10n/bundle.properties',
    'docs/*',
    '*readme.txt',
    'plugin.xml',
    'schema/*',
    'profile.list',
    'systembundle.properties',
    'version.txt',
    'xtend-gen/*',
]
APPEND_MERGE = [
    'plugin.properties',
    'META-INF/services/*',
    'META-INF/LICENSE.txt',
    'META-INF/LICENSE',
]
IGNORE_MERGE = [
    'eclipse32.png',
    'modeling32.png',
    'icons/*.png', # Assuming icons are always the same if they have the same name or de.* overrides org.*
    'icons/*.gif',
    'epl-v10.html',
    'org/osgi/service/log/*', # known duplicate in org.eclipse.osgi and org.eclipse.osgi.services,
    'META-INF/AL*',
    'META-INF/LGPL*',
    'META-INF/GPL*',
]

# Special klighd handling
KLIGHD_JARS_BLACKLIST = [
    'org.eclipse.ui*',
    'org.eclipse.e4*',
    'org.eclipse.*.ui*',
]
KLIGHD_JARS_WHITELIST = [
    'org.eclipse.ui.workbench_*',
    'org.eclipse.ui.ide_*', # For some reason IStorageEditorInput is required
]
KLIGHD_IGNORED_FILES = [
    'org/eclipse/ui/[!I]*', # Keep Interfaces for Klighd!
    'org/eclipse/ui/*/*',
    'org.eclipse.ui.ide*/icons/*',
    'fragment.properties',
]
klighd_swt = {}

def windows_safe_abspath(path):
    """Returns an absolute path that will not be invalidated by the
    Windows maximum path length.
    """
    if sys.platform == 'win32':
        return '\\\\?\\' + abspath(path)
    return abspath(path)

def main(args):
    print('-- Creating uber jar --')

    extracted = abspath(join(args.build, 'extracted'))
    merged = abspath(join(args.build, 'merged'))
    
    def assert_is_dir(s):
        if not isdir(s):
            stop('%s is not a directory or does not exist' % s)

    # Check input
    assert_is_dir(args.source)

    # Create build folders
    if isdir(extracted):
        shutil.rmtree(extracted)
    else:
        os.mkdir(extracted)
    if isdir(merged):
        shutil.rmtree(merged)
    else:
        os.mkdir(merged)

    target_dir = abspath(args.target)

    # Check klighd
    jars = os.listdir(args.source)
    klighd = any(jar.startswith('de.cau.cs.kieler.klighd') for jar in jars)
    if klighd:
        print('Detected Klighd. Activated special handling of Eclipse UI and SWT dependencies.')

    # Extract
    conflicts = extract(args, extracted, merged, klighd)
    if conflicts and not args.ignore_conflicts:
        stop('Stopping build due to merge conflicts.')

    # Bundle into jar
    jar = bundle(args, target_dir, merged, klighd)

    # Wrapper scripts
    if args.scripts:
        create_standalone_scripts(args, jar, target_dir,klighd)

def extract(args, extracted, merged, klighd):
    conflicts = False
    jars = sorted(os.listdir(args.source))
    processed_jars = [] # Tuples of plugin name and jar
    for jar in jars:
        if not jar.endswith('.jar'):
            print('Skipping file:', jar)
            continue
        elif klighd and any(fnmatch(jar, ign) for ign in KLIGHD_JARS_BLACKLIST) and not any(fnmatch(jar, req) for req in KLIGHD_JARS_WHITELIST):
            print('Skipping file (special klighd handling):', jar)
            continue
        elif jar.split("_")[0] in (p[0] for p in processed_jars):
            print('Multiple versions of the same plugin detected.')
            print('WARNING: This script does not support shading. Only the lower version of this plugin will be used (%s). This will cause runtime errors if any plugin requires a higher version API.' % next((p[1] for p in processed_jars if p[0] == jar.split("_")[0]), None))
            print('Skipping file:', jar)
            continue
        else:
            print('Extracting jar:', jar)
            target = abspath(join(extracted, jar[:-4]))
            if not isdir(target):
                os.makedirs(target)

            # Unpack jar
            check_call([args.jar, 'xf', abspath(join(args.source, jar))], cwd=target)

            if klighd and jar.startswith('org.eclipse.swt.'): # Do not merge swt fragments
                if 'gtk.linux.x86_64' in jar:
                    klighd_swt['linux'] = target
                elif 'win32.win32.x86_64' in jar:
                    klighd_swt['win'] = target
                elif 'cocoa.macosx.x86_64' in jar:
                    klighd_swt['osx'] = target
                else:
                    stop('Unknown platform-specific SWT fragment: ', jar)
                # Remove unwanted files from fragment directory
                for root, dirs, files in os.walk(target):
                    for file in (relpath(join(root, f), target) for f in files):
                        if any(fnmatch(file, pattern) for pattern in IGNORED_FILES) or any(fnmatch(file, pattern) for pattern in KLIGHD_IGNORED_FILES):
                            os.remove(join(target, file))
            else: # Merge content
                if not any(fnmatch(jar, ign) for ign in IGNORE_NESTED_JARS):
                    # Append nested jars for later unpacking
                    jars.extend(j for j in handleNestedJarsOnClasspath(target, args.source) if j not in jars)
                # Merge jar content into single folder
                for root, dirs, files in os.walk(target):
                    for file in (relpath(join(root, f), target) for f in files):
                        if any(fnmatch(file, pattern) for pattern in IGNORED_FILES):
                            continue #skip
                        if klighd and any(fnmatch(file, pattern) for pattern in KLIGHD_IGNORED_FILES):
                            continue #skip

                        src = join(target, file)
                        dest = join(merged, file)

                        if isfile(dest): # potential conflict
                            if any(fnmatch(file, match) for match in APPEND_MERGE): # merge by append
                                with open(src, 'r') as i:
                                    with open(dest, 'a') as o:
                                        o.write('\n')
                                        o.write(i.read())
                            elif any(fnmatch(file, match) for match in IGNORE_MERGE): # merge by ignoring overriders, assuming identical files ;)
                                pass
                            else:
                                errPrint('[ERROR] Could not merge', jar, 'Conflicting file:', file)
                                conflicts = True
                        else:
                            os.renames(windows_safe_abspath(src), windows_safe_abspath(dest))
                processed_jars.append((jar.split("_")[0], jar))
    return conflicts

def handleNestedJarsOnClasspath(dir, jars_dir):
    jars = []
    manifest = join(dir, join('META-INF', 'MANIFEST.MF'))
    if isfile(manifest):
        with open(manifest, 'r') as file:
            lines = file.readlines()
            classpath = None
            for line in lines:
                if 'Bundle-ClassPath:' in line: # start of classpath
                    startCP = line[17:].strip()
                    classpath = startCP if startCP else " " # assure truthy content if found
                elif classpath and ':' in line: # end of classpath
                    break
                elif classpath: # continue classpath collection
                    classpath += line.strip()
            if classpath:
                for cp in classpath.split(','):
                    cpFile = cp.strip()
                    if cpFile and cpFile != '.' and '.jar' in cpFile:
                        jarFile = join(dir, cpFile)
                        if isfile(jarFile):
                            print('Found nested jar on bundle class path: ', cpFile)
                            dest = join(jars_dir, basename(jarFile))
                            if isfile(dest):
                                continue # The nested jar is already in the directory
                                         # and does not need to be added again.
                            os.rename(jarFile, dest) # Move to input folder
                            jars.append(basename(jarFile))
                        else:
                            print('[Warning] Could not find file for nested jar on bundle class path: ', cpFile)
    return jars


def bundle(args, target_dir, merged, klighd):
    jar = join(target_dir, args.name + '.jar')
    print('Creating jar:', relpath(jar, target_dir))
    if not isdir(dirname(jar)):
        os.makedirs(dirname(jar))

    check_call([args.jar, 'cfe', jar, args.main, '.'], cwd=merged)

    if klighd and not args.noswt: # Include SWT
        jars = {}
        for platform in klighd_swt.keys():
            pjar = jar[:-4] + '.' + platform + '.jar'
            print('Creating jar:', relpath(pjar, target_dir))

            shutil.copy(jar, pjar) # copy SWT-less base jar
            check_call([args.jar, 'uf', pjar, '.'], cwd=klighd_swt[platform]) # Bundle platform-spefic SWT into new platform-spefic jar
            
            jars[platform] = pjar

        print('Removing jar:', relpath(jar, target_dir))
        os.remove(jar) # remove SWT-less base jar
        return jars
    else:
        return jar

def create_standalone_scripts(args, jar, target_dir, klighd):
    # This is some magic found in the depth of the internet by chsch
    print('-- Creating standalone scripts --')
    java9_options = ' --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/jdk.internal.loader=ALL-UNNAMED'

    if klighd and not args.noswt:
        jar_linux = jar['linux']
        jar_win = jar['win']
        jar_osx = jar['osx']
    else:
        jar_linux = jar_win = jar_osx = jar
    
    # linux
    if jar_linux:
        with open(jar_linux, 'rb') as jar_file:
            code = jar_file.read()
            linux_cmd = '#!/usr/bin/env bash\nexec java -Djava.system.class.loader=de.cau.cs.kieler.kicool.cli.CLILoader -Xmx512m %s -jar $0 "$@"\n'
            
            with open(join(target_dir, args.name + '-linux'), 'wb') as file:
                write_script(file, linux_cmd % java9_options, code)
            if args.java8:
                with open(join(target_dir, args.name + '-linuxJava8'), 'wb') as file:
                    write_script(file, linux_cmd % '', code)

    # windows
    if jar_win:
        with open(jar_win, 'rb') as jar_file:
            code = jar_file.read()
            win_cmd = 'java -Djava.system.class.loader=de.cau.cs.kieler.kicool.cli.CLILoader -Xmx512m %s -jar %%0 %%* \r\n exit /b %%errorlevel%%\r\n' # escaped percent sign because of format string!
            
            with open(join(target_dir, args.name + '-win.bat'), 'wb') as file:
                write_script(file, win_cmd % java9_options, code)
            if args.java8:
                with open(join(target_dir, args.name + '-winJava8.bat'), 'wb') as file:
                    write_script(file, win_cmd % '', code)
        
    # osx
    if jar_osx:
        with open(jar_osx, 'rb') as jar_file:
            code = jar_file.read()
            osx_cmd = '#!/usr/bin/env bash\nexec java -Djava.system.class.loader=de.cau.cs.kieler.kicool.cli.CLILoader -XstartOnFirstThread -Xmx512m %s -jar $0 "$@" \n'
            
            with open(join(target_dir, args.name + '-osx'), 'wb') as file:
                write_script(file, osx_cmd % java9_options, code)
            if args.java8:
                with open(join(target_dir, args.name + '-osxJava8'), 'wb') as file:
                    write_script(file, osx_cmd % '', code)

def write_script(file, command, code):
    print('Creating script', basename(file.name))
    file.write(command)
    file.write(code)
    flags = os.fstat(file.fileno()).st_mode
    flags |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.fchmod(file.fileno(), stat.S_IMODE(flags))

def stop(msg):
    errPrint('[ERROR] ' + msg)
    sys.exit(2)

def errPrint(*args, **kwargs):
    sys.stdout.flush() # ensure the context is clear
    print(*args, file=sys.stderr, **kwargs)
    sys.stderr.flush()

if __name__ == '__main__':
    argParser = argparse.ArgumentParser(description='This script bundles a self-contained eclipse update site into an executable uber-jar (no shading!) and several platform specific executable scripts bundling the jar.')
    argParser.add_argument('-s', dest='scripts', action='store_true', help='create platform specific standalone scripts of the jar')
    argParser.add_argument('-jar', default='jar', help='override jar command to adjust java version, e.g. /usr/lib/jvm/java-11-openjdk-amd64/bin/jar')
    argParser.add_argument('--java8', dest='java8', action='store_true', help='activate Java 8 support')
    argParser.add_argument('--noswt', dest='noswt', action='store_true', help='skips bundling platform specific SWT dependencies.')
    argParser.add_argument('--ignore-conflicts', dest='ignore_conflicts', action='store_true', help='prevents failing if merge fail due to a conflict.')
    argParser.add_argument('source', help='directory containing all plugins that should be bundled (self-contained update site)')
    argParser.add_argument('name', help='name of the generated executable jar/script')
    argParser.add_argument('main', help='main class of the generated jar')
    argParser.add_argument('target', help='target directory to store generated jar/ and scripts')
    argParser.add_argument('build', help='directory for storing intermediate results')
    try:
        main(argParser.parse_args())
    except KeyboardInterrupt:
        print('\nAbort')
        sys.exit(0)
